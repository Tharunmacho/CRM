"""Durable acknowledgements, reviewed document splitting and outgoing WhatsApp."""
from alembic import op

revision = "0016_intake_communications"
down_revision = "0015_attendance_hr"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_whatsapp_messages_conversation", "whatsapp_messages", ["phone_number_id", "sender_number", "received_at", "id"])
    op.execute("ALTER TABLE email_messages ADD COLUMN automation_headers JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("CREATE TABLE document_person_intakes (\n\tsource_type VARCHAR(20) NOT NULL, \n\tsource_id VARCHAR(36) NOT NULL, \n\towner_id VARCHAR(36), \n\tcreated_by VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'pending' NOT NULL, \n\trevision INTEGER DEFAULT '0' NOT NULL, \n\tsources JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tpages JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tignored_pages JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tlast_error TEXT, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_document_person_intake_source UNIQUE (source_type, source_id), \n\tCONSTRAINT ck_document_person_intake_source CHECK (source_type IN ('manual','email','whatsapp')), \n\tCONSTRAINT ck_document_person_intake_status CHECK (status IN ('pending','review','completed')), \n\tFOREIGN KEY(owner_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(created_by) REFERENCES auth_users (id)\n)")
    op.execute("COMMENT ON TABLE document_person_intakes IS 'Durable human review of multi-person source documents with immutable source provenance and page evidence.'")
    op.execute("CREATE INDEX ix_document_person_intakes_pending ON document_person_intakes (created_at) WHERE status = 'pending' AND last_error IS NULL")
    op.execute("CREATE INDEX ix_document_person_intakes_owner_history ON document_person_intakes (owner_id, updated_at, id) WHERE source_type = 'manual' AND status = 'completed'")
    op.execute("CREATE TABLE intake_acknowledgement_settings (\n\tsource_kind VARCHAR(20) NOT NULL, \n\tchannel_id VARCHAR(80) NOT NULL, \n\tenabled BOOLEAN DEFAULT false NOT NULL, \n\tbody TEXT NOT NULL, \n\tenabled_at TIMESTAMP WITH TIME ZONE, \n\tupdated_by VARCHAR(36), \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_intake_ack_settings_channel UNIQUE (source_kind, channel_id), \n\tCONSTRAINT ck_intake_ack_settings_source CHECK (source_kind IN ('email','whatsapp')), \n\tFOREIGN KEY(updated_by) REFERENCES auth_users (id)\n)")
    op.execute("COMMENT ON TABLE intake_acknowledgement_settings IS 'Admin-controlled acknowledgement templates; disabled by default and applicable only to new receipts.'")
    op.execute("CREATE TABLE whatsapp_templates (\n\tphone_number_id VARCHAR(80) NOT NULL, \n\tname VARCHAR(512) NOT NULL, \n\tlanguage VARCHAR(30) NOT NULL, \n\tcategory VARCHAR(30) NOT NULL, \n\tstatus VARCHAR(30) NOT NULL, \n\tcomponents JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tsynced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_whatsapp_template_language UNIQUE (phone_number_id, name, language), \n\tFOREIGN KEY(phone_number_id) REFERENCES whatsapp_channels (phone_number_id)\n)")
    op.execute("COMMENT ON TABLE whatsapp_templates IS 'Provider-synced approved template catalogue for messages outside the customer reply window.'")
    op.execute("CREATE TABLE document_person_groups (\n\tbundle_id VARCHAR(36) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\tlabel VARCHAR(200) NOT NULL, \n\tfields JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tpages JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'draft' NOT NULL, \n\tcandidate_id VARCHAR(36), \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_document_person_group_ordinal UNIQUE (bundle_id, ordinal), \n\tCONSTRAINT ck_document_person_group_status CHECK (status IN ('draft','committed')), \n\tFOREIGN KEY(bundle_id) REFERENCES document_person_intakes (id) ON DELETE CASCADE, \n\tFOREIGN KEY(candidate_id) REFERENCES crm_candidates (id)\n)")
    op.execute("COMMENT ON TABLE document_person_groups IS 'Reviewed person fields and exclusive page selections, committed atomically to candidate records.'")
    op.execute("CREATE INDEX ix_document_person_groups_candidate ON document_person_groups (candidate_id, bundle_id) WHERE status = 'committed'")
    op.execute('CREATE TABLE whatsapp_conversations (\n\tphone_number_id VARCHAR(80) NOT NULL, \n\tpeer_number VARCHAR(40) NOT NULL, \n\tdisplay_name VARCHAR(200), \n\tcandidate_id VARCHAR(36), \n\tlast_inbound_at TIMESTAMP WITH TIME ZONE, \n\tlast_message_at TIMESTAMP WITH TIME ZONE, \n\tlast_preview TEXT, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_whatsapp_conversation_peer UNIQUE (phone_number_id, peer_number), \n\tFOREIGN KEY(phone_number_id) REFERENCES whatsapp_channels (phone_number_id), \n\tFOREIGN KEY(candidate_id) REFERENCES crm_candidates (id)\n)')
    op.execute('CREATE INDEX ix_whatsapp_conversations_candidate_id ON whatsapp_conversations (candidate_id)')
    op.execute('CREATE INDEX ix_whatsapp_conversations_recent ON whatsapp_conversations (last_message_at, id)')
    op.execute("COMMENT ON TABLE whatsapp_conversations IS 'Business conversations with an explicit candidate link and last inbound time for the reply window.'")
    op.execute("CREATE TABLE whatsapp_send_attempts (\n\tconversation_id VARCHAR(36) NOT NULL, \n\trequest_key VARCHAR(64) NOT NULL, \n\tsender_user_id VARCHAR(36), \n\tsource_message_id VARCHAR(36), \n\tpayload_hash VARCHAR(64) NOT NULL, \n\tmessage_type VARCHAR(20) NOT NULL, \n\tbody TEXT, \n\ttemplate JSONB, \n\tobject_key VARCHAR(500), \n\tfile_name VARCHAR(255), \n\tcontent_type VARCHAR(120), \n\tcontent_hash VARCHAR(64), \n\tsize_bytes BIGINT, \n\tprovider_media_id VARCHAR(100), \n\tprovider_message_id VARCHAR(255), \n\tstate VARCHAR(20) DEFAULT 'queued' NOT NULL, \n\terror_code VARCHAR(80), \n\tlast_error TEXT, \n\taccepted_at TIMESTAMP WITH TIME ZONE, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tdelivered_at TIMESTAMP WITH TIME ZONE, \n\tread_at TIMESTAMP WITH TIME ZONE, \n\tfailed_at TIMESTAMP WITH TIME ZONE, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_whatsapp_send_state CHECK (state IN ('queued','preparing','sending','accepted','sent','delivered','read','failed','unknown','cancelled')), \n\tFOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations (id), \n\tUNIQUE (request_key), \n\tFOREIGN KEY(sender_user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(source_message_id) REFERENCES whatsapp_messages (id), \n\tUNIQUE (provider_message_id)\n)")
    op.execute('CREATE INDEX ix_whatsapp_send_attempts_conversation_id ON whatsapp_send_attempts (conversation_id)')
    op.execute("CREATE INDEX ix_whatsapp_send_queue ON whatsapp_send_attempts (created_at) WHERE state IN ('queued','preparing','sending')")
    op.execute("COMMENT ON TABLE whatsapp_send_attempts IS 'Durable outgoing WhatsApp intents; uncertain provider outcomes are never blindly resent.'")
    op.execute("CREATE TABLE intake_acknowledgements (\n\tsource_kind VARCHAR(20) NOT NULL, \n\tsource_id VARCHAR(36) NOT NULL, \n\tchannel_id VARCHAR(80) NOT NULL, \n\trecipient VARCHAR(255) NOT NULL, \n\tbody TEXT NOT NULL, \n\tsubject TEXT, \n\tstatus VARCHAR(20) DEFAULT 'pending' NOT NULL, \n\trequest_key VARCHAR(64) NOT NULL, \n\toutbound_id VARCHAR(36), \n\tprovider_message_id VARCHAR(255), \n\tattempts INTEGER DEFAULT '0' NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tlast_error TEXT, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_intake_ack_receipt UNIQUE (source_kind, source_id), \n\tCONSTRAINT ck_intake_ack_source CHECK (source_kind IN ('email','whatsapp')), \n\tCONSTRAINT ck_intake_ack_status CHECK (status IN ('pending','sending','sent','unknown','failed','suppressed','paused')), \n\tUNIQUE (request_key), \n\tFOREIGN KEY(outbound_id) REFERENCES whatsapp_send_attempts (id)\n)")
    op.execute('CREATE INDEX ix_intake_ack_queue ON intake_acknowledgements (status, created_at)')
    op.execute("COMMENT ON TABLE intake_acknowledgements IS 'One acknowledgement intent per successfully saved source receipt, with explicit uncertain-send recovery.'")
    op.execute('CREATE TABLE whatsapp_delivery_events (\n\tattempt_id VARCHAR(36), \n\tphone_number_id VARCHAR(80) NOT NULL, \n\tprovider_message_id VARCHAR(255) NOT NULL, \n\tcallback_data VARCHAR(64), \n\trecipient_number VARCHAR(40) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tevent_key VARCHAR(64) NOT NULL, \n\terror_code VARCHAR(80), \n\terror_detail TEXT, \n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(attempt_id) REFERENCES whatsapp_send_attempts (id), \n\tUNIQUE (event_key)\n)')
    op.execute('CREATE INDEX ix_whatsapp_delivery_events_attempt_id ON whatsapp_delivery_events (attempt_id)')
    op.execute('CREATE INDEX ix_whatsapp_delivery_events_provider_message_id ON whatsapp_delivery_events (provider_message_id)')
    op.execute("COMMENT ON TABLE whatsapp_delivery_events IS 'Deduplicated signed provider delivery events retained even when they precede the send response.'")

    # Existing receipts become readable conversations without guessing candidate identity.
    op.execute("""INSERT INTO whatsapp_conversations
        (id,phone_number_id,peer_number,display_name,last_inbound_at,last_message_at,last_preview)
        SELECT md5(phone_number_id || ':' || sender_number), phone_number_id, sender_number,
        (array_agg(sender_name ORDER BY received_at DESC,id DESC) FILTER (WHERE sender_name IS NOT NULL))[1],
        max(least(received_at,created_at)), max(received_at),
        (array_agg(coalesce(body,file_name,message_type) ORDER BY received_at DESC,id DESC))[1]
        FROM whatsapp_messages GROUP BY phone_number_id,sender_number
        ON CONFLICT (phone_number_id,peer_number) DO NOTHING""")


def downgrade():
    op.drop_index("ix_whatsapp_messages_conversation", table_name="whatsapp_messages")
    op.drop_table('whatsapp_delivery_events')
    op.drop_table('intake_acknowledgements')
    op.drop_table('whatsapp_send_attempts')
    op.drop_table('whatsapp_conversations')
    op.drop_table('document_person_groups')
    op.drop_table('whatsapp_templates')
    op.drop_table('intake_acknowledgement_settings')
    op.drop_table('document_person_intakes')
    op.drop_column("email_messages", "automation_headers")
