"""Documents as the owner would get them (plan §15.2 step 2).

One HTML/CSS layout per source, laid out with PyMuPDF's Story (the library the
app already uses): a thermal receipt, a vendor invoice, the card and bank
statements, the Square sales summary, DoorDash / Uber Eats / Grubhub payout
statements and a payroll summary. Checks and handwritten cash sheets are
drawn as images (degrade.py). CSV exports follow the columns csv_import.py
reads.

Every PDF carries SYNTHETIC TEST DATA in its metadata and a small footer, and
is byte-identical for the same input (fixed dates, no new ids).
"""

import html
import io

import pymupdf

from books.money import parse_cents
from ingest.pdf_utils import SYNTHETIC_MARK
from testdata import truth

LETTER = pymupdf.paper_rect("letter")
FIXED_DATE = "D:20260101000000"
FIXED_ID = "53594E54484554494354455354444154"      # "SYNTHETICTESTDAT"


def esc(x):
    return html.escape("" if x is None else str(x))


def money(x, dollar=True):
    cents = parse_cents(x) if not isinstance(x, int) else x
    if cents is None:
        return esc(x)            # "[?]" stays as printed
    neg = cents < 0
    t = f"{abs(cents) // 100:,}.{abs(cents) % 100:02d}"
    t = ("$" if dollar else "") + t
    return f"-{t}" if neg else t


def shown_money(value):
    """Amounts as printed; a value with [?] keeps its digits and the [?] marker position."""
    if isinstance(value, str) and "[?]" in value:
        return esc(value)
    return money(value)


def us_date(iso):
    if not iso:
        return ""
    return f"{iso[5:7]}/{iso[8:10]}/{iso[0:4]}"


# ------------------------------------------------------------------ PDF plumbing

def _story_pdf(pages_html, css, rect=LETTER, margin=40, auto_height=False):
    """Each item of pages_html starts a new page (overflow continues on more pages)."""
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    for body in pages_html:
        page_rect = rect
        if auto_height:
            probe = pymupdf.Story(html=body, user_css=css)
            _, filled = probe.place(pymupdf.Rect(0, 0, rect.width, 5000) + (margin, margin, -margin, -margin))
            page_rect = pymupdf.Rect(0, 0, rect.width, pymupdf.Rect(filled).y1 + margin + 18)
        story = pymupdf.Story(html=body, user_css=css)
        more = True
        while more:
            dev = writer.begin_page(page_rect)
            more, _ = story.place(page_rect + (margin, margin, -margin, -margin - 14))
            story.draw(dev)
            writer.end_page()
    writer.close()
    return buf.getvalue()


def finish(pdf_bytes, title="", footer=True):
    """Footer + metadata + deterministic bytes."""
    doc = pymupdf.open("pdf", pdf_bytes)
    if footer:
        for page in doc:
            r = page.rect
            page.insert_text((8, r.height - 6), SYNTHETIC_MARK + " - fictitious business, not a real document",
                             fontsize=5, color=(0.55, 0.55, 0.55))
    doc.set_metadata({"title": title, "author": "SBA test data generator", "subject": SYNTHETIC_MARK,
                      "keywords": SYNTHETIC_MARK, "creator": "testdata.render", "producer": "PyMuPDF",
                      "creationDate": FIXED_DATE, "modDate": FIXED_DATE})
    # A new PDF gets a random file ID when first saved; a fixed one keeps the bytes reproducible.
    doc.xref_set_key(-1, "ID", f"[<{FIXED_ID}><{FIXED_ID}>]")
    out = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    doc.close()
    return out


BASE_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #111; }
h1 { font-size: 15pt; margin: 0 0 4pt 0; } h2 { font-size: 11pt; margin: 8pt 0 3pt 0; }
table { width: 100%; border-collapse: collapse; }
td, th { padding: 2pt 3pt; vertical-align: top; } th { text-align: left; border-bottom: 1px solid #444; }
.r { text-align: right; } .small { font-size: 7.5pt; color: #444; } .b { font-weight: bold; }
.box { border: 1px solid #666; padding: 5pt; } .tot td { border-top: 1px solid #444; font-weight: bold; }
"""


# ------------------------------------------------------------------ receipts

VENDOR_ADDR = {
    "Restaurant Depot": "25 Brainard Rd, Hartford, CT 06114", "Costco": "1300 Berlin Tpke, Wethersfield, CT 06109",
    "Sysco Connecticut": "20 Stoney Hill Rd, Bethel, CT 06801", "CVS": "1099 Albany Ave, Hartford, CT 06112",
    "BJ's Wholesale": "3 Ella T Grasso Tpke, Windsor Locks, CT 06096", "Home Depot": "1 Weston St, Hartford, CT 06120",
}


def vendor_address(v):
    return VENDOR_ADDR.get(v, "Hartford, CT")


def _pay_line(ext):
    p = ext.get("payment") or {}
    m = p.get("method")
    if m == "card":
        return f"VISA ************{p.get('card_last4') or '0000'}"
    if m == "debit":
        return "DEBIT CARD ************0931"
    if m == "check":
        return f"CHECK #{p.get('check_no')}"
    if m == "petty_cash":
        return "CASH (PETTY)"
    return "CASH"


def thermal_receipt(ext, render):
    title = "RETURN / REFUND" if ext.get("is_refund") else "SALES RECEIPT"
    rows = "".join(f"<tr><td>{esc(l['description'])}{' T' if l.get('taxable') else ''}"
                   f"{'' if l.get('qty', 1) == 1 else ' x' + esc(l['qty'])}</td>"
                   f"<td class=r>{shown_money(l['amount'])}</td></tr>" for l in ext["lines"])
    extra = ""
    if ext.get("tip"):
        extra = f"<tr><td>TIP</td><td class=r>{shown_money(ext['tip'])}</td></tr>"
    body = f"""<div style="text-align:center"><b style="font-size:12pt">{esc(ext['vendor']).upper()}</b><br>
      {esc(vendor_address(ext['vendor']))}<br>{title}</div><br>
      <table><tr><td>{us_date(ext['date'])} 11:42</td><td class=r>INV {esc(ext.get('invoice_no'))}</td></tr></table>
      <hr><table>{rows}</table><hr>
      <table><tr><td>SUBTOTAL</td><td class=r>{shown_money(ext['subtotal'])}</td></tr>
      <tr><td>CT SALES TAX</td><td class=r>{shown_money(ext['tax'])}</td></tr>{extra}
      <tr><td class=b>TOTAL</td><td class="r b">{shown_money(ext['total'])}</td></tr>
      <tr><td>{esc(_pay_line(ext))}</td><td class=r>{shown_money(ext['total'])}</td></tr></table>
      <br><div style="text-align:center">T = TAXABLE ITEM<br>THANK YOU FOR SHOPPING</div>"""
    css = "body{font-family:monospace;font-size:8pt;} table{width:100%} .r{text-align:right} .b{font-weight:bold}"
    return _story_pdf([body], css, rect=pymupdf.Rect(0, 0, 230, 800), margin=14, auto_height=True)


def invoice(ext, render):
    pages = render.get("pages", 1)
    lines = ext["lines"]
    per = max(1, -(-len(lines) // pages))
    chunks = [lines[i:i + per] for i in range(0, len(lines), per)] or [[]]
    head = f"""<table><tr><td><h1>{esc(ext['vendor'])}</h1>{esc(vendor_address(ext['vendor']))}<br>Tel (860) 555-0140</td>
      <td class=r><h1>INVOICE</h1>No. {esc(ext.get('invoice_no'))}<br>Date {us_date(ext['date'])}</td></tr></table>
      <div class=box><b>Bill to:</b> {esc(truth.SHOP)}, {esc(truth.SHOP_ADDRESS)}</div><br>"""
    out = []
    for n, chunk in enumerate(chunks, 1):
        rows = "".join(f"<tr><td>{esc(l.get('qty', 1))}</td><td>{esc(l['description'])}</td>"
                       f"<td>{'Y' if l.get('taxable') else ''}</td><td class=r>{shown_money(l['amount'])}</td></tr>"
                       for l in chunk)
        body = (head if n == 1 else f"<p class=small>{esc(ext['vendor'])} - Invoice {esc(ext.get('invoice_no'))} "
                                    f"(continued) - page {n} of {len(chunks)}</p>")
        body += f"<table><tr><th>Qty</th><th>Description</th><th>Tax</th><th class=r>Amount</th></tr>{rows}</table>"
        if n == len(chunks):
            tip = f"<tr><td>Tip</td><td class=r>{shown_money(ext['tip'])}</td></tr>" if ext.get("tip") else ""
            body += f"""<br><table style="width:45%; margin-left:55%"><tr><td>Subtotal</td><td class=r>{shown_money(ext['subtotal'])}</td></tr>
              <tr><td>CT sales tax</td><td class=r>{shown_money(ext['tax'])}</td></tr>{tip}
              <tr class=tot><td>Total</td><td class=r>{shown_money(ext['total'])}</td></tr>
              <tr><td>Paid</td><td class=r>{esc(_pay_line(ext))}</td></tr></table>"""
        else:
            body += "<p class=small>Continued on next page</p>"
        out.append(body)
    return _story_pdf(out, BASE_CSS)


# ------------------------------------------------------------------ statements

def _txn_pages(title_html, rows, cols, per_page=32):
    pages = []
    for i in range(0, max(len(rows), 1), per_page):
        chunk = rows[i:i + per_page]
        head = title_html if i == 0 else "<p class=small>(continued)</p>"
        pages.append(head + f"<table><tr>{cols}</tr>{''.join(chunk)}</table>")
    return pages


def card_statement(ext, render):
    title = f"""<table><tr><td><h1>{esc(ext['issuer'])}</h1>{esc(truth.SHOP)}<br>{esc(truth.SHOP_ADDRESS)}</td>
      <td class=r>Account ending {esc(ext['card_last4'])}<br>Statement period {us_date(ext['period_start'])} -
      {us_date(ext['period_end'])}</td></tr></table>
      <div class=box><table><tr><td>Previous balance</td><td class=r>{money(ext['opening_balance'])}</td>
      <td>New balance</td><td class="r b">{money(ext['closing_balance'])}</td></tr></table></div>
      <h2>Transactions</h2>"""
    rows = [f"<tr><td>{us_date(t['date'])}</td><td>{esc(t['description'])}</td>"
            f"<td class=r>{money(t['amount'])}</td></tr>" for t in ext["transactions"]]
    rows.append(f"<tr class=tot><td></td><td>New balance</td><td class=r>{money(ext['closing_balance'])}</td></tr>")
    return _story_pdf(_txn_pages(title, rows, "<th>Posted</th><th>Description</th><th class=r>Amount</th>"), BASE_CSS)


def bank_statement(ext, render):
    title = f"""<table><tr><td><h1>{esc(ext['bank'])}</h1>Business Checking<br>{esc(truth.SHOP)}</td>
      <td class=r>Account ...{esc(ext['account_last4'])}<br>{us_date(ext['period_start'])} through
      {us_date(ext['period_end'])}</td></tr></table>
      <div class=box><table><tr><td>Beginning balance</td><td class=r>{money(ext['opening_balance'])}</td>
      <td>Ending balance</td><td class="r b">{money(ext['closing_balance'])}</td></tr></table></div>
      <h2>Account activity</h2>"""
    bal = parse_cents(ext["opening_balance"])
    rows = []
    for t in ext["transactions"]:
        amt = parse_cents(t["amount"])
        bal += amt or 0
        dep, wd = (money(amt, False), "") if amt and amt > 0 else ("", money(-(amt or 0), False))
        rows.append(f"<tr><td>{us_date(t['date'])}</td><td>{esc(t['description'])}</td>"
                    f"<td class=r>{dep}</td><td class=r>{wd}</td><td class=r>{money(bal, False)}</td></tr>")
    return _story_pdf(_txn_pages(title, rows, "<th>Date</th><th>Description</th><th class=r>Deposits</th>"
                                              "<th class=r>Withdrawals</th><th class=r>Balance</th>"), BASE_CSS)


def square_report(ext, render):
    rows = [("In-store / take-out sales", "sales_instore"), ("Delivery sales", "sales_delivery"),
            ("Delivery fees", "delivery_fees"), ("Gross sales", "gross_sales"), ("Discounts", "discounts"),
            ("Comps", "comps"), ("Refunds", "refunds"), ("Net sales", "net_sales"), ("Tax", "tax"),
            ("Tips", "tips"), ("Total collected", "total_collected"), ("Card", "tender_card"),
            ("Cash", "tender_cash"), ("Fees", "fees"), ("Net deposit (card)", "net_card_deposit")]
    body = f"""<h1>Square</h1><b>Sales Summary</b> - {esc(truth.SHOP)}<br>
      {us_date(ext['period_start'])}{'' if ext['period_start'] == ext['period_end'] else ' - ' + us_date(ext['period_end'])}
      12:00 AM - 11:59 PM<br><br><table>""" + "".join(
        f"<tr{' class=tot' if f in ('gross_sales', 'net_sales', 'total_collected', 'net_card_deposit') else ''}>"
        f"<td>{label}</td><td class=r>{shown_money(ext[f])}</td></tr>" for label, f in rows) + "</table>"
    return _story_pdf([body], BASE_CSS + "body{font-size:10pt}")


PLATFORM_STYLE = {"DoorDash": "#eb1700", "Uber Eats": "#06c167", "Grubhub": "#ff8000"}


def platform_statement(ext, render):
    color = PLATFORM_STYLE.get(ext["platform"], "#333")
    rows = [("Subtotal (food and drinks)", "subtotal"), ("Tax collected and remitted by the marketplace",
                                                          "tax_collected_by_platform"),
            ("Commission", "commission"), ("Marketing fees", "marketing_fees"),
            ("Promotions paid by the merchant", "promotions"), ("Adjustments / error charges", "adjustments"),
            ("Net payout", "payout")]
    body = f"""<h1 style="color:{color}">{esc(ext['platform'])}</h1><b>Merchant payout statement</b><br>
      {esc(truth.SHOP)}<br>Orders {us_date(ext['period_start'])} - {us_date(ext['period_end'])};
      paid {us_date(ext['payout_date'])} to account ...{truth.BANK_LAST4}<br><br><table>""" + "".join(
        f"<tr{' class=tot' if f == 'payout' else ''}><td>{label}</td><td class=r>"
        f"{'-' if f in ('commission', 'marketing_fees', 'promotions') and parse_cents(ext[f]) else ''}"
        f"{shown_money(ext[f])}</td></tr>" for label, f in rows) + \
        "</table><p class=small>Marketplace facilitator: the platform collected and remitted CT sales tax on these orders.</p>"
    return _story_pdf([body], BASE_CSS + "body{font-size:10pt}")


def payroll_report(ext, render):
    rows = [("Gross wages", "gross_wages"), ("of which tips paid through payroll", "tips_paid"),
            ("Employee taxes and withholding", "employee_withholding"), ("Net pay", "net_pay"),
            ("Employer taxes", "employer_taxes"), ("Total debited from your account", "total_debit")]
    body = f"""<h1>{esc(ext['provider'])}</h1><b>Payroll summary</b> - {esc(truth.SHOP)}<br>
      Pay period {us_date(ext['period_start'])} - {us_date(ext['period_end'])}; pay date {us_date(ext['pay_date'])}<br><br>
      <table>""" + "".join(f"<tr{' class=tot' if f == 'total_debit' else ''}><td>{label}</td>"
                            f"<td class=r>{shown_money(ext[f])}</td></tr>" for label, f in rows) + "</table>"
    return _story_pdf([body], BASE_CSS + "body{font-size:10pt}")


def flyer(render):
    body = """<h1 style="font-size:28pt;text-align:center">GRAND RE-OPENING!</h1>
      <p style="font-size:16pt;text-align:center">Hartford Slice Pizza<br>2 large cheese pizzas $24.99<br>
      Free garlic knots with any order over $30</p><p style="text-align:center">Call (860) 555-0199</p>"""
    return _story_pdf([body], BASE_CSS)


def blank_page(render):
    return _story_pdf(["<p> </p>"], BASE_CSS)


LAYOUTS = {"thermal": thermal_receipt, "invoice": invoice, "card_statement": card_statement,
           "bank_statement": bank_statement, "square": square_report, "platform": platform_statement,
           "payroll": payroll_report}


def text_pdf(spec):
    """The clean (digital) PDF of one document, before any scan effects. It prints the
    true figures; an illegible digit is covered later by degrade.obscure_digit()."""
    ext = spec["extraction"]
    layout = spec["render"].get("layout")
    return LAYOUTS[layout](ext, spec["render"])


# ------------------------------------------------------------------ CSV exports

def square_csv(specs):
    cols = ["Date", "In-store Sales", "Delivery Sales", "Delivery Fees", "Gross Sales", "Discounts", "Comps",
            "Refunds", "Net Sales", "Tax", "Tips", "Total Collected", "Card", "Cash", "Fees", "Net Deposit"]
    fields = ["sales_instore", "sales_delivery", "delivery_fees", "gross_sales", "discounts", "comps", "refunds",
              "net_sales", "tax", "tips", "total_collected", "tender_card", "tender_cash", "fees", "net_card_deposit"]
    out = [f"# {SYNTHETIC_MARK}", ",".join(cols)]
    for spec in specs:
        e = spec["extraction"]
        out.append(",".join([us_date(e["period_start"])] + [e[f] for f in fields]))
    return ("\n".join(out) + "\n").encode("utf-8")


def bank_csv(ext):
    out = [f"# {SYNTHETIC_MARK}", f"# Bank: {ext['bank']}", f"# Account: ...{ext['account_last4']}",
           f"# Period start: {ext['period_start']}", f"# Period end: {ext['period_end']}",
           f"# Opening balance: {ext['opening_balance']}", "Date,Description,Amount,Check Number,Balance"]
    bal = parse_cents(ext["opening_balance"])
    for t in ext["transactions"]:
        bal += parse_cents(t["amount"])
        desc = t["description"].replace(",", " ")
        out.append(f"{us_date(t['date'])},{desc},{t['amount']},{t.get('check_no') or ''},{truth.s(bal)}")
    return ("\n".join(out) + "\n").encode("utf-8")
