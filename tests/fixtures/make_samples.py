"""Generate the sample / acceptance-test PDFs and keyword files.

Run:  python -m tests.fixtures.make_samples [output_dir]
"""
import os
import random
import sys

import pymupdf as fitz
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, FrameBreak, ListFlowable, ListItem,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

ss = getSampleStyleSheet()
TITLE = ParagraphStyle("T", parent=ss["Title"], fontSize=22, spaceAfter=14)
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=16, spaceBefore=10, spaceAfter=8, textColor=colors.HexColor("#1f3864"))
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, spaceBefore=8, spaceAfter=6, textColor=colors.HexColor("#2e5597"))
BODY = ParagraphStyle("B", parent=ss["BodyText"], fontSize=10.5, leading=14, spaceAfter=8)
CAP = ParagraphStyle("C", parent=BODY, fontName="Helvetica-Bold", fontSize=10, spaceAfter=4)
NOTE = ParagraphStyle("N", parent=BODY, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))

OPRISK_INTRO = ("Operational risk is the risk of loss resulting from inadequate or failed internal processes, people "
                "and systems, or from external events. Northwind maintains an enterprise framework to identify, assess, "
                "monitor and mitigate these exposures across every business line.")


def table(rows, widths=None, header_bg="#d9e2f3", repeat=0):
    t = Table(rows, colWidths=widths, repeatRows=repeat)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#444444")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def cell(text):
    return Paragraph(text, ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=10, spaceAfter=0))


def bullets(items):
    return ListFlowable([ListItem(Paragraph(i, BODY), leftIndent=12) for i in items], bulletType="bullet",
                        start="\u2022", leftIndent=14)


def _header_footer(title):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(inch, letter[1] - 0.55 * inch, title)
        canvas.drawRightString(letter[0] - inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def build_main_report(path):
    doc = BaseDocTemplate(path, pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                          title="Northwind Annual Operations Review")
    w, h = letter
    fw = w - 2 * inch
    one = Frame(inch, inch, fw, h - 2 * inch, id="one")
    top = Frame(inch, h - inch - 70, fw, 70, id="top")
    gap = 18
    colw = (fw - gap) / 2
    left = Frame(inch, inch, colw, h - 2 * inch - 70, id="left")
    right = Frame(inch + colw + gap, inch, colw, h - 2 * inch - 70, id="right")
    hf = _header_footer("Northwind Holdings - Annual Operations Review 2025")
    doc.addPageTemplates([PageTemplate("one", [one], onPage=hf), PageTemplate("two", [top, left, right], onPage=hf)])

    s = []
    # Page 1: unrelated content
    s += [Paragraph("Annual Operations Review 2025", TITLE),
          Paragraph("1. Company Overview", H1),
          Paragraph("Northwind Holdings was founded in 1998 as a regional distributor of household goods. Today the "
                    "group employs 4,200 people across eleven countries and operates fourteen distribution centres.", BODY),
          Paragraph("During the year the group opened new offices in Lisbon and Warsaw and relocated its headquarters "
                    "to a larger campus with improved public transport links for staff.", BODY),
          Paragraph("2. Marketing and Brand Strategy", H1),
          Paragraph("Our marketing campaign increased brand awareness among young consumers by 18 percent. Social media "
                    "channels and influencer partnerships delivered the strongest engagement, while print advertising "
                    "was scaled back.", BODY),
          Paragraph("The brand refresh introduced a new logo, packaging colours and a loyalty programme that rewards "
                    "repeat purchases with seasonal discounts.", BODY),
          Paragraph("Table 1: Marketing Budget by Channel", CAP),
          table([["Channel", "2024 Budget", "2025 Budget", "Change"],
                 ["Social media", "$1.2m", "$1.8m", "+50%"],
                 ["Television", "$2.0m", "$1.6m", "-20%"],
                 ["Print", "$0.6m", "$0.3m", "-50%"],
                 ["Sponsorships", "$0.4m", "$0.5m", "+25%"]], [1.6 * inch] * 4),
          PageBreak()]

    # Pages 2-3: operational risk discussion spanning pages
    s += [Paragraph("3. Operational Risk Management", H1),
          Paragraph(OPRISK_INTRO, BODY),
          Paragraph("3.1 Process Failures and Control Weaknesses", H2),
          Paragraph("Several process breakdowns were observed in the accounts payable function. Manual hand-offs between "
                    "teams caused duplicate supplier payments, and reconciliations were frequently completed late.", BODY),
          Paragraph("Internal audit identified control weaknesses in user access reviews: leavers retained system access "
                    "for up to 60 days, and segregation of duties was not enforced for payment approvals.", BODY),
          Paragraph("The main internal control deficiencies identified during the year were:", BODY),
          bullets(["Lack of maker-checker approval on high-value payments.",
                   "Incomplete documentation of key reconciliations.",
                   "Unmonitored changes to production configuration.",
                   "Insufficient training for staff on incident escalation."]),
          Paragraph("Each deficiency was assigned an owner, a remediation deadline and a residual rating that is reported "
                    "monthly to the risk committee until closure.", BODY),
          Paragraph("Management also reviewed loss events recorded in the incident database. Losses from execution errors "
                    "and delivery failures totalled $2.4m, a reduction of 12 percent compared to the previous year.", BODY),
          Paragraph("Root cause analysis showed that most loss events originated from manual workarounds introduced when "
                    "automated controls were temporarily disabled during system upgrades.", BODY),
          Paragraph("3.2 Case Study: Payment System Outage", H2),
          Paragraph("In March a server outage in the payment processing platform halted customer transactions for six "
                    "hours. The incident was triggered by an untested configuration change deployed outside the approved "
                    "change window.", BODY),
          Paragraph("The incident response team restored service using the disaster recovery site. A post-incident review "
                    "found that monitoring alerts were routed to an unattended mailbox, delaying escalation by two hours.", BODY),
          Paragraph("Table 2: Risk Incident Analysis", CAP),
          table([["Incident", "Root cause", "Control failure", "Remediation action"],
                 [cell("Payment platform outage"), cell("Untested configuration change"), cell("Change approval bypassed"),
                  cell("Mandatory change advisory board sign-off")],
                 [cell("Duplicate supplier payments"), cell("Manual invoice re-keying"), cell("No duplicate check"),
                  cell("Automated duplicate detection")],
                 [cell("Delayed incident escalation"), cell("Alerts sent to unattended mailbox"), cell("Monitoring gap"),
                  cell("24x7 on-call rota and alert routing")],
                 [cell("Leaver access retained"), cell("HR feed not integrated"), cell("Access review ineffective"),
                  cell("Daily automated de-provisioning")]],
                [1.45 * inch, 1.6 * inch, 1.45 * inch, 1.7 * inch]),
          Paragraph("Note: incidents are classified according to the group incident taxonomy.", NOTE),
          Paragraph("3.3 Management Recommendations", H2),
          Paragraph("Management recommends strengthening operational resilience by testing recovery plans quarterly, "
                    "automating key controls and linking remediation progress to executive objectives.", BODY),
          PageBreak()]

    # Page 4: unrelated
    s += [Paragraph("4. Employee Wellness Programme", H1),
          Paragraph("The wellness programme offered yoga classes, nutrition workshops and subsidised gym memberships. "
                    "Participation rose to 46 percent of employees, and staff satisfaction surveys improved.", BODY),
          Paragraph("Table 3: Wellness Participation", CAP),
          table([["Activity", "Participants", "Satisfaction"],
                 ["Yoga classes", "820", "4.6 / 5"],
                 ["Nutrition workshops", "410", "4.3 / 5"],
                 ["Gym membership", "1,150", "4.1 / 5"]], [2.1 * inch] * 3),
          Spacer(1, 12),
          Paragraph("5. Data Quality and Governance", H1),
          Paragraph("Reliable reporting depends on complete, accurate and timely information. During the year missing "
                    "values and inconsistent customer records reduced the reliability of several management reports.", BODY),
          Paragraph("A data governance council now owns validation rules, data lineage documentation and the resolution "
                    "of records that fail completeness and accuracy checks.", BODY),
          Paragraph("Table 4: Quarterly System Metrics", CAP),
          table([["Metric", "Q1", "Q2", "Q3", "Q4"],
                 ["Platform uptime", "99.2%", "99.6%", "99.8%", "99.9%"],
                 ["Average response time", "320 ms", "290 ms", "270 ms", "250 ms"],
                 ["Service desk tickets", "1,240", "1,100", "980", "950"],
                 ["Data quality exceptions", "312", "260", "190", "145"]], [1.9 * inch] + [1.07 * inch] * 4),
          NextPageTemplate("two"),
          PageBreak()]

    # Page 5: two-column layout (AI left, unrelated facilities right)
    s += [Paragraph("6. Technology and Facilities Update", H1), FrameBreak(),
          Paragraph("Machine Learning in Fraud Detection", H2),
          Paragraph("The analytics team deployed a machine learning model that scores card transactions in real time. "
                    "The neural network was trained on three years of labelled fraud cases.", BODY),
          Paragraph("Model predictions are explained to investigators using feature attributions, and the algorithm is "
                    "retrained monthly to adapt to new fraud patterns.", BODY),
          Paragraph("A model risk policy requires independent validation of every artificial intelligence system before "
                    "it is used in production decisions.", BODY),
          FrameBreak(),
          Paragraph("Cafeteria Renovation", H2),
          Paragraph("The staff cafeteria at headquarters was renovated with new seating, a salad bar and extended opening "
                    "hours. Feedback from employees has been very positive.", BODY),
          Paragraph("Parking facilities were expanded with 120 additional spaces and electric vehicle charging points for "
                    "visitors and staff.", BODY),
          NextPageTemplate("one"),
          PageBreak()]

    # Pages 6-7: multi-page control deficiency register
    reg = [["ID", "Control area", "Deficiency", "Remediation", "Status"]]
    areas = ["Payments", "Access management", "Change management", "Reconciliations", "Vendor management"]
    issues = ["Missing approval evidence", "Access review overdue", "Emergency change not documented",
              "Unreconciled balance ageing", "Contract renewal without due diligence"]
    fixes = ["Implement dual approval", "Quarterly certification", "Retrospective CAB review",
             "Weekly ageing report", "Vendor risk assessment"]
    for i in range(1, 57):
        reg.append([f"CD-{i:03d}", areas[i % 5], issues[i % 5], fixes[i % 5], "Open" if i % 3 else "Closed"])
    s += [Paragraph("7. Control Deficiency Register", H1),
          Paragraph("The register below lists all control deficiencies tracked by the second line of defence.", BODY),
          Paragraph("Table 5: Control Deficiency Register", CAP),
          table(reg, [0.6 * inch, 1.2 * inch, 1.9 * inch, 1.6 * inch, 0.7 * inch], repeat=1),
          PageBreak()]

    # Final page: appendix with a duplicated paragraph
    s += [Paragraph("Appendix A: Glossary", H1),
          Paragraph(OPRISK_INTRO, BODY),
          Paragraph("Brand equity refers to the commercial value derived from consumer perception of the brand name.", BODY)]
    doc.build(s)


def build_second_report(path):
    doc = BaseDocTemplate(path, pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch)
    doc.addPageTemplates([PageTemplate("one", [Frame(inch, inch, letter[0] - 2 * inch, letter[1] - 2 * inch)],
                                       onPage=_header_footer("Northwind Data Office - Quarterly Bulletin"))])
    s = [Paragraph("Quarterly Data Office Bulletin", TITLE),
         Paragraph("Customer Master Data Remediation", H1),
         Paragraph("The customer master data clean-up removed 48,000 duplicate customer records and corrected postal "
                   "addresses that failed validation against the national address file.", BODY),
         Paragraph("Data stewards now review completeness dashboards weekly, and any dataset that falls below a 98 percent "
                   "accuracy score is escalated to the data owner.", BODY),
         Paragraph("Office Events", H1),
         Paragraph("The summer picnic took place in July with over 300 attendees, live music and a charity raffle.", BODY)]
    doc.build(s)


def build_scanned_report(path, dpi=200):
    tmp = path + ".src.pdf"
    doc = BaseDocTemplate(tmp, pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch)
    doc.addPageTemplates([PageTemplate("one", [Frame(inch, inch, letter[0] - 2 * inch, letter[1] - 2 * inch)])])
    big = ParagraphStyle("big", parent=BODY, fontSize=12, leading=16)
    s = [Paragraph("Incident Review Memorandum", TITLE),
         Paragraph("Warehouse System Failure", H1),
         Paragraph("On 14 May the warehouse management system failed during the night shift. Orders could not be "
                   "picked for nine hours and 2,300 deliveries were delayed. The root cause was an expired security "
                   "certificate that had not been renewed.", big),
         Paragraph("Remediation measures include automated certificate monitoring and an updated escalation procedure.", big),
         Spacer(1, 10),
         Paragraph("Table 1: Incident Remediation Plan", CAP),
         Table([["Action", "Owner", "Due date"],
                ["Certificate monitoring", "IT Operations", "30 June"],
                ["Escalation procedure", "Risk Office", "15 July"],
                ["Staff training", "HR", "31 July"]], colWidths=[2.4 * inch, 1.8 * inch, 1.4 * inch],
               style=TableStyle([("GRID", (0, 0), (-1, -1), 1.2, colors.black),
                                 ("FONTSIZE", (0, 0), (-1, -1), 11),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)])),
         Spacer(1, 14),
         Paragraph("Canteen Menu", H1),
         Paragraph("The canteen will serve a new vegetarian menu every Wednesday starting next month.", big)]
    doc.build(s)
    src = fitz.open(tmp)
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("png"))
    out.save(path)
    out.close()
    src.close()
    os.remove(tmp)


def build_protected(path, src):
    doc = fitz.open(src)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="secret123")
    doc.close()


def build_corrupted(path):
    rnd = random.Random(7)
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n" + bytes(rnd.getrandbits(8) for _ in range(4000)))


def build_keyword_files(folder):
    import pandas as pd

    kws = ["control weakness", "incident", "remediation", "data validation", "machine learning", "root cause"]
    with open(os.path.join(folder, "sample_keywords.txt"), "w") as fh:
        fh.write("\n".join(kws) + "\n")
    pd.DataFrame({"keyword": kws}).to_csv(os.path.join(folder, "sample_keywords.csv"), index=False)
    pd.DataFrame({"Keyword": kws}).to_excel(os.path.join(folder, "sample_keywords.xlsx"), index=False)


def build_all(folder):
    os.makedirs(folder, exist_ok=True)
    paths = {
        "main": os.path.join(folder, "annual_operations_review.pdf"),
        "second": os.path.join(folder, "data_office_bulletin.pdf"),
        "scanned": os.path.join(folder, "scanned_incident_memo.pdf"),
        "protected": os.path.join(folder, "protected_report.pdf"),
        "corrupted": os.path.join(folder, "corrupted.pdf"),
        "not_pdf": os.path.join(folder, "not_a_pdf.pdf"),
    }
    build_main_report(paths["main"])
    build_second_report(paths["second"])
    build_scanned_report(paths["scanned"])
    build_protected(paths["protected"], paths["second"])
    build_corrupted(paths["corrupted"])
    with open(paths["not_pdf"], "w") as fh:
        fh.write("This is plain text, not a PDF.")
    build_keyword_files(folder)
    return paths


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "generated")
    for k, v in build_all(out).items():
        print(k, v)
