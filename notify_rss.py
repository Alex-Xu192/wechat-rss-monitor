import os
import smtplib
import subprocess
from email.mime.text import MIMEText

MAIL_HOST = os.environ.get("MAIL_HOST", "smtp.qq.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "465"))
MAIL_USER = os.environ.get("MAIL_USER")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("RECEIVER")


def send_email(title, content):
    msg = MIMEText(content, "plain", "utf-8")
    msg["From"] = MAIL_USER
    msg["To"] = RECEIVER
    msg["Subject"] = title

    with smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT) as server:
        server.login(MAIL_USER, MAIL_PASS)
        server.sendmail(MAIL_USER, [RECEIVER], msg.as_string())


def changed_files():
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "HEAD")

    if base and not base.startswith("000000"):
        cmd = ["git", "diff", "--name-only", f"{base}..{head}"]
    else:
        cmd = ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", head]

    output = subprocess.check_output(cmd, text=True, encoding="utf-8")
    return [line.strip() for line in output.splitlines() if line.strip()]


files = [
    path for path in changed_files()
    if path.startswith("rss/") and path.endswith(".txt") and os.path.exists(path)
]

if not files:
    print("No RSS TXT files changed.")
    raise SystemExit(0)

parts = []
for path in files:
    with open(path, "r", encoding="utf-8") as f:
        parts.append(f"文件：{path}\n\n{f.read()}")

content = "\n\n==============================\n\n".join(parts)
send_email(f"RSS 更新：{len(files)} 个 TXT 汇总文件", content)
print(f"Plain text email sent to {RECEIVER}, files: {len(files)}")
