"""Escaped, printable tax invoices from the immutable issued snapshot; no payment claims."""
from html import escape
from io import BytesIO
from decimal import Decimal


def amount_words(value) -> str:
    """Exact INR wording for the invoice's decimal total, not a payment receipt."""
    small = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven',
             'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
    def words(number):
        if number < 20:
            return small[number]
        if number < 100:
            return tens[number // 10] + (' ' + small[number % 10] if number % 10 else '')
        for scale, label in ((10000000, 'Crore'), (100000, 'Lakh'), (1000, 'Thousand'), (100, 'Hundred')):
            if number >= scale:
                return words(number // scale) + ' ' + label + (' ' + words(number % scale) if number % scale else '')
    paise_total = int(Decimal(str(value)) * 100)
    rupees, paise = divmod(paise_total, 100)
    return 'Rupees ' + words(rupees) + (' and ' + words(paise) + ' Paise' if paise else '') + ' Only'


def render(snapshot: dict) -> bytes:
    import pymupdf
    issuer, recipient, sums = snapshot['issuer'], snapshot['recipient'], snapshot['amounts']
    def e(value):
        return escape(str(value or '')).replace('\n', '<br>')
    bank = ''
    if issuer.get('bankName') or issuer.get('bankAccount'):
        bank = f"<p>Bank: {e(issuer.get('bankName'))}<br>Account: {e(issuer.get('bankAccount'))}<br>IFSC: {e(issuer.get('bankIfsc'))}</p>"
    html = f"""<h1>{e(issuer['companyName'])}</h1><p class="issuer">{e(issuer['address'])}<br>
        GSTIN: {e(issuer['gstin'])} &nbsp; CIN: {e(issuer.get('cin'))}<br>{e(issuer.get('phone'))}<br>
        {e(issuer.get('email'))} &nbsp; {e(issuer.get('website'))}</p>
        <h2>TAX INVOICE</h2><table><tr><td><b>Invoice no.</b> {e(snapshot['number'])}</td>
        <td><b>Date</b> {e(snapshot['date'])}</td></tr></table>
        <p><b>Bill to</b><br>{e(recipient['name'])}<br>{e(recipient.get('address'))}<br>
        Candidate: {e(recipient['ref'])}<br>Passport: {e(recipient.get('passport'))}</p>
        <p>Place of supply: {e(issuer.get('placeOfSupply'))}<br>Order: {e(snapshot.get('orderRef'))}</p>
        <table><tr><th>Description</th><th>SAC</th><th class="amount">Amount (INR)</th></tr>
        <tr><td>{e(snapshot.get('description') or 'Recruitment service fee')}</td><td>{e(issuer.get('sacCode'))}</td><td class="amount">{e(sums['subtotal'])}</td></tr>
        <tr><td colspan="2">CGST ({e(sums['cgstRate'])}%)</td><td class="amount">{e(sums['cgst'])}</td></tr>
        <tr><td colspan="2">SGST ({e(sums['sgstRate'])}%)</td><td class="amount">{e(sums['sgst'])}</td></tr>
        <tr class="total"><td colspan="2">Total invoice amount (INR)</td><td class="amount">{e(sums['total'])}</td></tr></table>
        <p><b>Amount in words:</b> {e(amount_words(sums['total']))}</p>
        {bank}<p class="note">This invoice records the service charge and taxes. Payment receipt is recorded separately.</p>
        <table class="signatures"><tr><td><br><br>Signature of Emigrant / Remitter</td>
        <td>For {e(issuer['companyName'])}<br><br><br>Authorised signatory</td></tr></table>
        <p class="note">This is a Computer Generated Invoice.</p>"""
    css = 'body{font-family:sans-serif;font-size:10pt;color:#17253b}h1{font-size:20pt;text-align:center;margin-bottom:6pt}h2{text-align:center;font-size:15pt;margin:20pt 0}.issuer{text-align:center;font-size:9pt}table{width:100%;border-collapse:collapse;margin:12pt 0}td,th{border:.6pt solid #cad4df;padding:9pt;text-align:left}th,.total{background:#eef3f8}.amount{text-align:right}.note{font-size:8pt;color:#526078;margin-top:22pt}.signature{text-align:right;margin-top:26pt}'
    stream = BytesIO()
    story = pymupdf.Story(html=html, user_css=css)
    writer = pymupdf.DocumentWriter(stream)
    page = pymupdf.Rect(0, 0, 612, 792)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(page + (38, 34, -38, -34))
        story.draw(device)
        writer.end_page()
    writer.close()
    return stream.getvalue()
