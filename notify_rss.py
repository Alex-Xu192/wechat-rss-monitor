import os
import re
import smtplib
import subprocess
from dataclasses import dataclass
from email.mime.text import MIMEText

MAIL_HOST = os.environ.get("MAIL_HOST", "smtp.qq.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "465"))
MAIL_USER = os.environ.get("MAIL_USER")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("RECEIVER")
ARTICLES_PER_EMAIL = 8

SECTION_SEPARATOR = re.compile(r"(?m)^[ \t]*={20,}[ \t]*\r?$")
ARTICLE_SEPARATOR = re.compile(r"(?m)^[ \t]*-{20,}[ \t]*\r?$")
ARTICLE_TITLE = re.compile(r"^\s*\d+[.)、]\s*(.+?)\s*$")
ARTICLE_TEXT_LABEL = re.compile(r"(?m)^([ \t]*)【(?:摘要节选|摘要)】")


@dataclass(frozen=True)
class Article:
    title: str
    content: str


@dataclass(frozen=True)
class Digest:
    report_title: str
    source: str
    time_window: str
    articles: list[Article]


def _article_blocks(text, source_path):
    blocks = []
    for part in ARTICLE_SEPARATOR.split(text.strip()):
        part = part.strip()
        if not part:
            continue

        first_line = part.splitlines()[0].strip()
        if ARTICLE_TITLE.match(first_line):
            blocks.append(part)
        elif blocks:
            # Keep separator-like lines that are part of an article's own text.
            blocks[-1] += "\n------------------------------\n" + part
        else:
            raise ValueError(
                f"{source_path}: expected a numbered article after the header, "
                f"got {first_line!r}"
            )
    return blocks


def parse_digest(text, source_path="<input>"):
    sections = SECTION_SEPARATOR.split(text, maxsplit=1)
    if len(sections) != 2:
        raise ValueError(f"{source_path}: missing header/detail separator")

    header, detail_text = sections
    header_lines = [line.strip() for line in header.splitlines() if line.strip()]
    report_title = header_lines[0] if header_lines else "RSS 中文新闻汇总"

    source_match = re.search(r"(?m)^【来源】\s*(.*?)\s*$", header)
    time_match = re.search(r"(?m)^【时间】\s*(.*?)\s*$", header)
    count_match = re.search(r"(?m)^【数量】\s*(\d+)\s*$", header)
    if not count_match:
        raise ValueError(f"{source_path}: missing numeric 【数量】 field")

    source = source_match.group(1).strip() if source_match else "RSS"
    time_window = time_match.group(1).strip() if time_match else ""
    declared_count = int(count_match.group(1))

    raw_articles = _article_blocks(detail_text, source_path)
    if declared_count != len(raw_articles):
        raise ValueError(
            f"{source_path}: header says {declared_count} articles, "
            f"but parsed {len(raw_articles)}"
        )

    articles = []
    for block in raw_articles:
        first_line = block.splitlines()[0].strip()
        title_match = ARTICLE_TITLE.match(first_line)
        if not title_match:
            raise ValueError(f"{source_path}: article title is not numbered")
        articles.append(Article(title=title_match.group(1).strip(), content=block))

    return Digest(
        report_title=report_title,
        source=source,
        time_window=time_window,
        articles=articles,
    )


def chunk_articles(articles, batch_size=ARTICLES_PER_EMAIL):
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    return [articles[i:i + batch_size] for i in range(0, len(articles), batch_size)]


def _format_article(article, number):
    lines = article.content.splitlines()
    title_match = ARTICLE_TITLE.match(lines[0].strip())
    title = title_match.group(1).strip() if title_match else article.title
    lines[0] = f"{number}. {title}"
    content = "\n".join(lines)
    return ARTICLE_TEXT_LABEL.sub(r"\1【新闻正文】", content)


def _email_body(digest, batch, batch_index, batch_count, batch_size):
    start = (batch_index - 1) * batch_size + 1
    end = start + len(batch) - 1
    lines = [
        digest.report_title,
        "",
        f"【来源】{digest.source}",
        f"【时间】{digest.time_window}",
        f"【本轮数量】{len(digest.articles)}",
        f"【本封数量】{len(batch)}",
        f"【邮件批次】第{batch_index}/{batch_count}封（新闻{start}–{end}）",
        "",
        "【本封新闻标题汇总】",
    ]
    lines.extend(f"{i}. {article.title}" for i, article in enumerate(batch, start=1))
    lines.extend(["", "==============================", ""])

    article_text = "\n\n------------------------------\n\n".join(
        _format_article(article, number)
        for number, article in enumerate(batch, start=1)
    )
    return "\n".join(lines) + article_text


def build_email_messages(digest, batch_size=ARTICLES_PER_EMAIL):
    batches = chunk_articles(digest.articles, batch_size)
    messages = []
    for batch_index, batch in enumerate(batches, start=1):
        subject = (
            f"【RSS简报】{digest.source}｜{len(batch)}条新闻｜"
            f"第{batch_index}/{len(batches)}封"
        )
        messages.append(
            (
                subject,
                _email_body(
                    digest, batch, batch_index, len(batches), batch_size
                ),
            )
        )
    return messages


def send_emails(messages):
    if not messages:
        return

    missing = [
        name for name, value in (
            ("MAIL_USER", MAIL_USER),
            ("MAIL_PASS", MAIL_PASS),
            ("RECEIVER", RECEIVER),
        ) if not value
    ]
    if missing:
        raise RuntimeError("Missing required mail environment variables: " + ", ".join(missing))

    with smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT) as server:
        server.login(MAIL_USER, MAIL_PASS)
        for subject, content in messages:
            msg = MIMEText(content, "plain", "utf-8")
            msg["From"] = MAIL_USER
            msg["To"] = RECEIVER
            msg["Subject"] = subject
            server.sendmail(MAIL_USER, [RECEIVER], msg.as_string())
            print(f"Plain text email sent: {subject}")


def changed_files():
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "HEAD")

    if base and not base.startswith("000000"):
        cmd = ["git", "diff", "--name-only", f"{base}..{head}"]
    else:
        cmd = ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", head]

    output = subprocess.check_output(cmd, text=True, encoding="utf-8")
    return [line.strip() for line in output.splitlines() if line.strip()]


def main():
    files = sorted({
        path for path in changed_files()
        if path.startswith("rss/") and path.endswith(".txt") and os.path.exists(path)
    })
    if not files:
        print("No RSS TXT files changed.")
        return 0

    messages = []
    for path in files:
        with open(path, "r", encoding="utf-8-sig") as file:
            digest = parse_digest(file.read(), path)
        if not digest.articles:
            print(f"Skipping {path}: no RSS articles.")
            continue
        messages.extend(build_email_messages(digest))

    if not messages:
        print("No RSS articles to email.")
        return 0

    send_emails(messages)
    print(f"Sent {len(messages)} plain text email(s) to {RECEIVER}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
