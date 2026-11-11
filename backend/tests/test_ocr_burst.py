"""A synthetic burst through real uploads, leases, provider checkpoints and form autofill."""
from collections import Counter
from copy import deepcopy
import re
import threading
import time
from types import SimpleNamespace

import pymupdf as fitz
from sqlalchemy import func, select, text
from veriis import VerisOCRRateLimitError, VerisOCRServerError

from app import document_extraction_worker
from app.db.models import Attachment, Candidate, DocumentExtractionJob, Source
from app.services import application_form, document_extraction, manual_extraction
from app.services.document_storage import read_bytes
from test_application_form import desk, form, upload  # noqa: F401
from test_database_naming import schema_database  # noqa: F401
from test_document_extraction import pdf, result
from test_ocr_classification import CV


def test_burst_keeps_every_file_and_paid_job_bound_to_its_person_across_capacity_and_out_of_order_results(desk, monkeypatch):
    # Uploads stamp their instance from the shared config; use the same isolated storage instance as this worker.
    monkeypatch.setattr('app.core.config.get_settings', lambda: desk.settings)
    count = 12
    guard = threading.Lock()
    submits, accepted, completions = Counter(), {}, []
    state = {'complete': False, 'active': 0, 'peak': 0}
    originals = {}

    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            assert mode == 'resume'
            with fitz.open(stream=document.data, filetype='pdf') as sent:
                index = int(re.search(r'BURST-(\d+)', sent[0].get_text())[1])
            with guard:
                submits[idempotency_key] += 1
                attempt = submits[idempotency_key]
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            try:
                time.sleep(0.05)
                if index % 3 == 0 and attempt == 1:
                    if index % 2:
                        raise VerisOCRRateLimitError('synthetic busy', status=429, retry_after=30)
                    raise VerisOCRServerError('synthetic busy', status=503, code='job_queue_full', retry_after=30)
                with guard:
                    assert idempotency_key not in accepted, 'A checkpointed accepted job must never be submitted again'
                    accepted[idempotency_key] = f'burst-provider-{index}'
                return SimpleNamespace(job_id=f'burst-provider-{index}', status='queued')
            finally:
                with guard:
                    state['active'] -= 1

        def get(self, job_id):
            index = int(job_id.rsplit('-', 1)[1])
            if index % 2 and not state['complete']:
                return SimpleNamespace(status='running')
            payload = result('resume', 1)
            payload.update(name=f'Burst Person {index:02}', contact={'emails': [f'burst{index:02}@example.test']},
                           personal_info={'father_name': f'Father {index:02}'})
            with guard:
                completions.append(index)
            return SimpleNamespace(status='succeeded', result=payload)

    class Provider:
        def __init__(self, **_):
            self.jobs = Jobs()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    monkeypatch.setattr(document_extraction, 'VerisOCR', Provider)
    with desk.sessions() as db:
        for index in range(count):
            db.add(Candidate(id=f'burst-{index}', ref=f'BURST-{index}', name=f'Burst Person {index:02}',
                             phone=f'00000000{index:02}', source=Source.walk_in, owner_id='staff',
                             father_name='Reviewed father' if index == 0 else None,
                             field_sources={'personal.fatherName': 'manual'} if index == 0 else {}))
        db.commit()
    for index in range(count):
        originals[index] = pdf(CV.replace('Alex Example', f'Burst Person {index:02}')
                               .replace('alex@example.test', f'burst{index:02}@example.test') + f'\nBURST-{index}\n')
        response = upload(desk, 'resume', originals[index], candidate=f'burst-{index}')
        assert response.status_code == 202

    def cycle():
        document_extraction_worker.run_cycle(sessions=desk.sessions, settings=desk.settings, concurrency=4)

    cycle()
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        assert len(jobs) == count
        assert {job.status for job in jobs} == {'completed', 'retry'}
        assert all(job.attempts == 0 and job.claimed_by is None and job.lease_until is None for job in jobs)
        assert len(accepted) == 8 and len(completions) == 4
        accepted_before = {job.id: job.state['provider_jobs']['resume'].get('job_id') for job in jobs}
        for job in jobs:
            index = int(job.candidate_id.removeprefix('burst-'))
            assert read_bytes(desk.settings, job.object_key) == originals[index]
            if job.status == 'completed':
                assert db.get(Candidate, job.candidate_id).father_name == f'Father {index:02}'
            else:
                assert job.finished_at is None
        finished = next(job for job in jobs if job.status == 'completed')
        waiting = next(job for job in jobs if job.status == 'retry')
    assert form(desk, finished.candidate_id)['documents']['resume']['extraction']['status'] == 'done'
    assert form(desk, waiting.candidate_id)['documents']['resume']['extraction']['status'] != 'done'
    assert state['peak'] >= 2, 'The burst must actually overlap provider work'

    state['complete'] = True
    future = document_extraction._wall() + 60
    monkeypatch.setattr(document_extraction, '_wall', lambda: future)
    with desk.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET next_attempt_at=now()-interval '1 second' WHERE status='retry'"))
        db.commit()
    cycle()
    assert len(accepted) == count and len(set(accepted.values())) == count
    assert sorted(submits.values()) == [1] * 8 + [2] * 4
    assert set(completions[:4]).issubset({2, 4, 8, 10})
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        assert len(jobs) == count and all(job.status == 'completed' and job.attempts == 0 for job in jobs)
        document_count = db.scalar(select(func.count()).select_from(Attachment))
        for job in jobs:
            index = int(job.candidate_id.removeprefix('burst-'))
            person = db.get(Candidate, job.candidate_id)
            assert person.email == f'burst{index:02}@example.test'
            assert person.father_name == ('Reviewed father' if index == 0 else f'Father {index:02}')
            assert job.state['provider_jobs']['resume']['job_id'] == f'burst-provider-{index}'
            if accepted_before[job.id]:
                assert job.state['provider_jobs']['resume']['job_id'] == accepted_before[job.id]
            application_form.apply_job_output(db, job, deepcopy(job.state['output']))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Attachment)) == document_count
    for job in jobs:
        manual_extraction.process_job(job.id, sessions=desk.sessions, settings=desk.settings)
    assert len(accepted) == count and sum(submits.values()) == 16
