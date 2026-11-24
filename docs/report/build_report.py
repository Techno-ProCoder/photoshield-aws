"""Builds the project report.

  python docs/report/build_report.py               public version  -> docs/report/PhotoShield_Report.docx
  python docs/report/build_report.py --submission  with roll numbers -> notes/submission/PhotoShield_Report.docx

Roll numbers are read from notes/team.json, which is git-ignored so they never reach the public repository.
"""
import json
import os
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
ROOT = os.path.dirname(DOCS)
PUBLIC_OUT = os.path.join(HERE, "PhotoShield_Report.docx")
SUBMISSION_OUT = os.path.join(ROOT, "notes", "submission", "PhotoShield_Report.docx")
ROLL_NUMBERS = os.path.join(ROOT, "notes", "team.json")
FONT = "Cambria"
BLACK = RGBColor(0x1A, 0x1A, 0x1A)
THEME_ATTRS = ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme")
TEAM = ["Gavisht Singh", "Aarpit Kumar Roul", "Nishant Bimra"]
INSTRUCTOR = "Dr. Varinder Pal Singh"


def set_font(style, size, bold=False, italic=False):
    style.font.name = FONT
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = italic
    style.font.color.rgb = BLACK
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in THEME_ATTRS:
        rfonts.attrib.pop(qn(attr), None)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), FONT)


def keep_pictures_sharp(doc):
    # Stop Word from downsampling images when it saves or exports the PDF.
    settings = doc.settings.element
    el = OxmlElement("w:doNotAutoCompressPictures")
    settings.insert(0, el)


def border(parent, tag, edges):
    borders = OxmlElement(tag)
    for edge, size in edges.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single" if size else "nil")
        if size:
            el.set(qn("w:sz"), str(size))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "1A1A1A")
        borders.append(el)
    parent.append(borders)


def body(doc, text, after=6, keep=False):
    p = doc.add_paragraph(text)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.keep_with_next = keep
    return p


def caption(doc, label, text, keep=False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.keep_with_next = keep
    r = p.add_run(label + " ")
    r.bold = True
    r.font.size = Pt(9.5)
    r = p.add_run(text)
    r.font.size = Pt(9.5)
    return p


def figure(doc, path, width_cm, label, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(path, width=Cm(width_cm))
    caption(doc, label, text)


def table(doc, label, text, header, rows, widths_cm):
    """Three-rule table: rule above, rule under the header, rule below. No fills, no verticals."""
    c = caption(doc, label, text, keep=True)
    c.paragraph_format.space_after = Pt(3)
    t = doc.add_table(rows=1 + len(rows), cols=len(header))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    border(t._tbl.tblPr, "w:tblBorders",
           {"top": 10, "left": 0, "bottom": 10, "right": 0, "insideH": 0, "insideV": 0})
    for r_idx, values in enumerate([header] + rows):
        row = t.rows[r_idx]
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for c_idx, value in enumerate(values):
            cell = row.cells[c_idx]
            cell.width = Cm(widths_cm[c_idx])
            if r_idx == 0:
                border(cell._tc.get_or_add_tcPr(), "w:tcBorders", {"bottom": 5})
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.keep_with_next = r_idx < len(rows)
            run = p.add_run(value)
            run.font.size = Pt(9.5)
            run.bold = r_idx == 0
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)
    spacer.runs or spacer.add_run("").font.size  # empty spacer line
    spacer.paragraph_format.line_spacing = Pt(6)


def numbered(doc, items):
    for i, item in enumerate(items, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.8)
        p.paragraph_format.first_line_indent = Cm(-0.5)
        p.paragraph_format.space_after = Pt(1)
        p.add_run(f"{i}.\t{item}")
        p.paragraph_format.tab_stops.add_tab_stop(Cm(0.8))
    doc.paragraphs[-1].paragraph_format.space_after = Pt(6)


def page_number(paragraph):
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        el = OxmlElement("w:fldChar" if kind else "w:instrText")
        if kind:
            el.set(qn("w:fldCharType"), kind)
        else:
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)
    run.font.size = Pt(9)


def build(out, rolls=None):
    doc = Document()
    keep_pictures_sharp(doc)
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.top_margin = sec.bottom_margin = Cm(2.2)
    sec.left_margin = sec.right_margin = Cm(2.3)

    set_font(doc.styles["Normal"], 10.5)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.12
    for name, size, before, after in (("Heading 1", 12, 12, 4), ("Heading 2", 10.5, 8, 2)):
        st = doc.styles[name]
        set_font(st, size, bold=True)
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    page_number(footer)

    # Title block
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run("PhotoShield: A Highly Available Photo-Privacy Web Service on AWS")
    r.bold = True
    r.font.size = Pt(15)
    for line, after in (("Project report, UCS531 Cloud Computing", 0),
                        ("Thapar Institute of Engineering and Technology, Patiala", 8)):
        p = doc.add_paragraph(line)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(after)

    # Submitted by / submitted to, side by side without visible borders.
    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    border(t._tbl.tblPr, "w:tblBorders",
           {"top": 0, "left": 0, "bottom": 0, "right": 0, "insideH": 0, "insideV": 0})
    left, right = t.rows[0].cells
    left.width, right.width = Cm(9.5), Cm(6.9)
    blocks = (
        (left, "Submitted by", [f"{name} ({rolls[name]})" if rolls else name for name in TEAM]),
        (right, "Submitted to", [INSTRUCTOR]),
    )
    for cell, heading, lines in blocks:
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(1)
        p.add_run(heading).bold = True
        for line in lines:
            q = cell.add_paragraph(line)
            q.paragraph_format.space_after = Pt(0)
    right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for q in right.paragraphs[1:]:
        q.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    p = doc.add_paragraph("November 2026")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(6)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    border(p._p.get_or_add_pPr(), "w:pBdr", {"bottom": 6})

    # Abstract
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.left_indent = p.paragraph_format.right_indent = Cm(0.8)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run("Abstract. ")
    r.bold = True
    p.add_run(
        "This report describes the design, implementation and evaluation of PhotoShield, a web application "
        "deployed on Amazon Web Services (AWS) so that it remains available when an instance or an Availability "
        "Zone fails and scales with demand. The application inspects uploaded photographs for embedded metadata, "
        "such as GPS coordinates and device serial numbers, and returns a copy with that metadata removed. All 39 "
        "infrastructure resources are defined in Terraform. In a 15-minute load test the web tier scaled from two "
        "to four instances and served 6,676 requests without an error, and after an instance was terminated full "
        "capacity was restored in 3.1 minutes.")

    # 1
    doc.add_heading("1. Introduction", level=1)
    body(doc, "A web application that runs on a single server is unavailable whenever that server, or the data "
              "centre hosting it, fails, and it degrades when demand exceeds the capacity of one machine. Our "
              "proposal was to build an application on AWS that tolerates such failures and adjusts its capacity "
              "automatically, using only resources that fall within the free tier or its credit allowance.")
    body(doc, "To exercise the infrastructure we built a practical application. Digital photographs carry EXIF and "
              "XMP metadata written by the camera: the exact position where the photo was taken, the device model "
              "and often its serial number, and the time of capture. Anyone who receives the original file can "
              "read this information. PhotoShield reports what a photo discloses and produces a copy that contains "
              "only the image data. The workload needs object storage for files, a database for records and "
              "CPU-intensive processing, which makes the scaling behaviour observable under realistic use.")
    body(doc, "The objectives of the project were:", after=2, keep=True)
    numbered(doc, [
        "Run the web tier on at least two instances in two Availability Zones, with no single point of failure.",
        "Detect unhealthy instances and replace them automatically.",
        "Scale the number of instances with CPU utilisation.",
        "Keep the database private and keep credentials out of code and off the servers.",
        "Define the infrastructure as code so that it can be rebuilt on demand and removed when idle.",
        "Measure scaling and recovery behaviour with repeatable tests.",
    ])

    # 2
    doc.add_heading("2. System architecture", level=1)
    body(doc, "Requests reach an internet-facing Application Load Balancer (ALB), which forwards each one to a "
              "healthy instance in either Availability Zone. The instances belong to an Auto Scaling group and are "
              "stateless: uploaded and processed files are kept in Amazon S3 and records in an Amazon RDS MySQL "
              "database, so any instance can be removed without loss of data. Supporting services provide "
              "credentials (Secrets Manager), permissions (IAM), monitoring and alerts (CloudWatch and SNS) and "
              "administrative access (Systems Manager). Figure 1 shows the architecture and Table 1 lists the "
              "configuration of each component.")
    figure(doc, os.path.join(DOCS, "architecture.png"), 16.0, "Figure 1.",
           "System architecture. Arrows show the request path; labels give the ports permitted by each security group.")
    table(doc, "Table 1.", "Components and their configuration.",
          ["Component", "Service", "Configuration"], [
              ["Load balancer", "Application Load Balancer", "HTTP on port 80; health check GET /health every 15 s, unhealthy after 2 failures"],
              ["Compute", "EC2 Auto Scaling group", "t3.micro instances, Amazon Linux 2023; 2 to 4 instances across us-east-1a and us-east-1b; target tracking at 50% average CPU"],
              ["Database", "RDS MySQL 8.4", "db.t3.micro, single-AZ, not publicly accessible, encrypted storage, daily backups"],
              ["Storage", "Amazon S3", "Private bucket, server-side encryption, HTTPS-only policy, objects expire after 24 hours"],
              ["Credentials", "Secrets Manager, IAM", "Database password managed by RDS; instance role with least-privilege permissions"],
              ["Operations", "CloudWatch, SNS, Systems Manager", "1-minute metrics, dashboard and alarms; shell access through Session Manager"],
          ], [2.4, 4.0, 10.0])

    # 3
    doc.add_heading("3. Implementation", level=1)
    doc.add_heading("3.1 Infrastructure as code", level=2)
    body(doc, "The infrastructure is written in Terraform with the AWS provider and organised by concern: network, "
              "identity, storage, database, load balancing, compute and monitoring. Terraform derives the order "
              "of creation from references between resources and records the result in a state file, which is "
              "excluded from version control. Two scripts wrap the workflow: one builds the stack and waits until "
              "every instance passes its health check, which takes 11 to 14 minutes, most of it spent creating the "
              "database; the other removes every resource. The stack is therefore only running while it is in use.")
    doc.add_heading("3.2 Application", level=2)
    body(doc, "PhotoShield is a Python application built with Flask and served by gunicorn on port 8000 under an "
              "unprivileged system account. The image module uses the Pillow library to read GPS coordinates from "
              "EXIF and XMP data, converting degrees, minutes and seconds to decimal degrees, together with the "
              "device, lens, serial number, capture time and software. Each photo is classed as high, medium or low "
              "risk according to what it reveals. To remove the metadata, the module applies the orientation tag to "
              "the pixels and then constructs a new image from the raw pixel data, so that no metadata block can be "
              "copied across.")
    body(doc, "The cleaned copy is written to S3 and offered to the user through a pre-signed URL that expires after "
              "ten minutes. Each scan is recorded in the database as a set of flags and counts; the coordinates "
              "themselves are never stored. Uploads are limited to 15 MB, images larger than 40 megapixels are "
              "rejected before they are decoded, and all queries are parameterised. The code is covered by 27 "
              "automated tests in which S3 and MySQL are replaced by test doubles.", keep=True)
    figure(doc, os.path.join(DOCS, "results", "screenshot-result-crop.png"), 9.5, "Figure 2.",
           "Result page for a test photograph tagged with a GPS position and a phone model.")
    doc.add_heading("3.3 Security", level=2)
    body(doc, "Network access is restricted by security groups that refer to one another: the load balancer accepts "
              "HTTP from the internet, the instances accept port 8000 only from the load balancer, and the database "
              "accepts port 3306 only from the instances. The database password is generated by RDS and held in "
              "Secrets Manager; the application obtains it at run time through the instance role and fetches it "
              "again after a rotation. The role allows the instances to read the application code but to write only "
              "under the prefix used for processed photos. Instances require IMDSv2, which prevents the theft of "
              "role credentials through server-side request forgery, have no SSH access or key pairs, and connect "
              "to the database over TLS with certificate verification. Data at rest is encrypted in RDS and S3.")
    doc.add_heading("3.4 Deployment of new versions", level=2)
    body(doc, "Terraform uploads the application to S3, and each new instance downloads it at boot, since EC2 user "
              "data is limited to 16 KB. A hash of the application files is part of the launch template, so a code "
              "change produces a new template version and starts a rolling instance refresh that replaces one "
              "instance at a time while at least half of the capacity remains in service.")

    # 4
    doc.add_heading("4. Evaluation", level=1)
    body(doc, "All tests ran against the deployed system in the us-east-1 region from a client in India. The load "
              "and failover tests are scripts in the project repository, and the graphs are drawn from CloudWatch "
              "data at one-minute resolution. Table 2 summarises the results.", keep=True)
    table(doc, "Table 2.", "Summary of test results.",
          ["Test", "Method", "Result"], [
              ["Deployment", "Build the stack until all targets are healthy", "11 to 14 minutes"],
              ["Rolling update", "Deploy a new version; probe the site every 10 s", "14.5 minutes; 79 of 79 requests succeeded"],
              ["Load test", "24 concurrent clients for 15 minutes, 6 MP images", "6,676 requests, none failed; 2 to 4 instances; 4.7 to 9.6 requests/s"],
              ["Failover", "Terminate 1 of 2 instances; probe every second", "Full capacity after 184 s; 5 of 177 requests failed, all within 21 s"],
              ["Processing", "Single request on a t3.micro instance", "About 465 ms for a 6 MP image; 45 to 90 ms for 1 to 1.6 MP"],
          ], [2.6, 6.4, 7.4])
    doc.add_heading("4.1 Scaling", level=2)
    body(doc, "Figure 3 shows the load test. Average CPU utilisation rose to about 90% within two minutes of the start of the test. The "
              "target-tracking policy raised the desired capacity from two to four instances 4.6 minutes after the "
              "start, and the new instances entered service about two minutes later. Throughput doubled and median "
              "latency fell from 5.0 s to between 1.7 and 2.5 s, with no failed requests. CPU utilisation remained "
              "near 88% afterwards because the group had reached its configured maximum of four instances, which "
              "acts as a cost limit. One-minute instance metrics were required for this behaviour: with the default "
              "five-minute metrics the policy would not have reacted for about fifteen minutes.")
    figure(doc, os.path.join(DOCS, "results", "scaling.png"), 13.5, "Figure 3.",
           "Load test: average CPU utilisation, instances in service and requests per minute.")
    doc.add_heading("4.2 Failure recovery", level=2)
    body(doc, "When one of two instances was terminated abruptly, five requests failed during the first 21 seconds. "
              "In that interval the load balancer was still routing some requests to the terminated instance, "
              "because it marks a target unhealthy only after two failed checks 15 seconds apart. The Auto Scaling "
              "group launched a replacement after 85 seconds and full capacity was restored after 3.1 minutes. "
              "Planned replacements did not show this effect: during the rolling update, instances were removed "
              "from the load balancer before they were stopped, and no request failed. A shorter health-check "
              "interval would reduce the error window at the cost of more health-check traffic.")

    # 5
    doc.add_heading("5. Cost", level=1)
    body(doc, "The running system costs about USD 2 per day, mainly for the load balancer and the database. Because "
              "it is removed after each session, development and all of the tests above used only a few dollars of "
              "AWS credit. The design choices that keep the cost low are t3.micro instances, a single-AZ database, "
              "the default VPC's public subnets instead of private subnets with a NAT gateway (about USD 32 per "
              "month), a 24-hour expiry on stored photos and a limit of four instances.")

    # 6
    doc.add_heading("6. Limitations and future work", level=1)
    body(doc, "The database runs in a single Availability Zone and is the remaining single point of failure; enabling "
              "Multi-AZ deployment would add a synchronous standby with automatic failover at roughly twice the "
              "database cost. The site is served over HTTP only, and HTTPS would require a domain with an ACM "
              "certificate or a CloudFront distribution. Moving the instances to private subnets with VPC endpoints "
              "would remove their public addresses. Further work could add rate limiting with AWS WAF, a queue for "
              "processing very large images, and a CI/CD pipeline that tests and deploys each change.")

    # 7
    doc.add_heading("7. Conclusion", level=1)
    body(doc, "PhotoShield meets the objectives of the proposal using free-tier-sized resources. The web tier has no "
              "single point of failure across two Availability Zones, recovers from the loss of an instance in about "
              "three minutes, doubles its capacity automatically under load and accepts new versions without failed "
              "requests. The system can be rebuilt from code at any time and applies least privilege throughout. "
              "The single-AZ database is the main remaining weakness and is a deliberate trade-off against cost.")

    doc.add_heading("References", level=1)
    refs = [
        "Amazon Web Services. Health checks for Application Load Balancer target groups. Elastic Load Balancing User Guide.",
        "Amazon Web Services. Target tracking scaling policies and instance refresh. Amazon EC2 Auto Scaling User Guide.",
        "Amazon Web Services. Multi-AZ deployments and password management with AWS Secrets Manager. Amazon RDS User Guide.",
        "Amazon Web Services. Instance Metadata Service Version 2 and burstable performance instances. Amazon EC2 User Guide.",
        "HashiCorp. AWS Provider documentation. Terraform Registry.",
        "Camera & Imaging Products Association. CIPA DC-008, Exchangeable image file format for digital still cameras (Exif 2.32).",
    ]
    for i, ref in enumerate(refs, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.7)
        p.paragraph_format.first_line_indent = Cm(-0.7)
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(f"[{i}]  {ref}")
        r.font.size = Pt(9.5)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    doc.save(out)
    print("Saved", out)


if __name__ == "__main__":
    if "--submission" in sys.argv:
        with open(ROLL_NUMBERS, encoding="utf-8") as fh:
            build(SUBMISSION_OUT, json.load(fh))
    else:
        build(PUBLIC_OUT)
