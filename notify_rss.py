import hashlib
import json
import os
import random
import re
import smtplib
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

MAIL_HOST = os.environ.get("MAIL_HOST", "smtp.163.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", "465"))
MAIL_USER = os.environ.get("MAIL_USER", "alex653@163.com")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("RECEIVER", "alex@emalex666.dpdns.org")
ARTICLES_PER_EMAIL = 8
STATE_PATH = Path(os.environ.get("RSS_STATE_PATH", ".rss-notify-state.json"))
BJT = timezone(timedelta(hours=8))

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


def build_combined_email_message(digests, bjt_time_str):
    non_empty = [d for d in digests if d.articles]
    if not non_empty:
        return []

    # Merge digests of the same source while preserving order
    grouped_sources = []
    source_articles = {}
    for d in non_empty:
        if d.source not in source_articles:
            grouped_sources.append(d.source)
            source_articles[d.source] = []
        source_articles[d.source].extend(d.articles)

    total_articles = sum(len(arts) for arts in source_articles.values())
    subject = f"【RSS汇总】{bjt_time_str}｜共{len(grouped_sources)}个来源 {total_articles}条新闻"

    lines = [
        f"RSS 定时汇总简报（{bjt_time_str}）",
        "",
        f"【汇总时间】{bjt_time_str}（北京时间）",
        f"【来源数量】{len(grouped_sources)} 个（{', '.join(grouped_sources)}）",
        f"【新闻总数】{total_articles} 条",
        "",
        "【本期各来源新闻标题目录】",
    ]

    for src in grouped_sources:
        arts = source_articles[src]
        lines.append(f"\n▌{src}（共 {len(arts)} 条）")
        for idx, art in enumerate(arts, start=1):
            lines.append(f"  {idx}. {art.title}")

    lines.extend(["", "============================================================", ""])

    detail_sections = []
    for src in grouped_sources:
        arts = source_articles[src]
        sec_lines = [
            f"============================== 【来源：{src}（共 {len(arts)} 条）】 ==============================",
            "",
        ]
        formatted_articles = "\n\n------------------------------\n\n".join(
            _format_article(art, idx) for idx, art in enumerate(arts, start=1)
        )
        sec_lines.append(formatted_articles)
        detail_sections.append("\n".join(sec_lines))

    body = "\n".join(lines) + "\n\n\n".join(detail_sections)
    return [(subject, body)]


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


def load_state():
    if not STATE_PATH.exists():
        return {"last_sent_sha": "", "last_window_key": "", "sent_files": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"last_sent_sha": "", "last_window_key": "", "sent_files": []}


def save_state(state):
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_head_sha():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, encoding="utf-8"
    ).strip()


def commit_exists(sha):
    if not sha:
        return False
    res = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
    )
    return res.returncode == 0


def changed_files(state):
    base = os.environ.get("BASE_SHA", "") or state.get("last_sent_sha", "")
    head = os.environ.get("HEAD_SHA", "HEAD")

    if base and not base.startswith("000000") and commit_exists(base):
        cmd = ["git", "diff", "--name-only", f"{base}..{head}"]
    else:
        cmd = ["git", "log", "--since=8 hours ago", "--name-only", "--pretty=format:", head]

    output = subprocess.check_output(cmd, text=True, encoding="utf-8")
    already_sent = set(state.get("sent_files", []))
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and line.strip() not in already_sent
    ]


def check_schedule_window(state):
    """
    If triggered by GitHub Actions schedule, enforce at most 1 email per window
    at a daily randomized minute inside:
      - 08:00-10:00 BJT (morning)
      - 14:00-16:00 BJT (afternoon)
      - 20:00-22:00 BJT (evening)
    Returns (should_run, window_key, bjt_now).
    """
    bjt_now = datetime.now(BJT)

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        return True, f"{bjt_now.strftime('%Y-%m-%d-%H%M')}-manual", bjt_now

    hour = bjt_now.hour
    # Target random time is picked within 08:00-09:55, 14:00-15:55, 20:00-21:55 BJT.
    # Allow a 1-hour grace tail (until 10:59, 16:59, 22:59 BJT) so delayed cron/push never misses the window.
    if 8 <= hour < 11:
        window_name, start_hour = "morning", 8
    elif 14 <= hour < 17:
        window_name, start_hour = "afternoon", 14
    elif 20 <= hour < 23:
        window_name, start_hour = "evening", 20
    else:
        print(f"Current BJT {bjt_now.strftime('%Y-%m-%d %H:%M')} is outside 8-10/14-16/20-22 windows.")
        return False, "", bjt_now

    window_key = f"{bjt_now.strftime('%Y-%m-%d')}-{window_name}"
    if state.get("last_window_key") == window_key:
        print(f"Already processed window {window_key} today, skipping.")
        return False, window_key, bjt_now

    # Deterministically pick a random target minute [0..95] within the 120-minute window
    digest_hex = hashlib.sha256(window_key.encode("utf-8")).hexdigest()[:8]
    target_minute = int(digest_hex, 16) % 96
    elapsed_minutes = (hour - start_hour) * 60 + bjt_now.minute
    is_last_slot = elapsed_minutes >= 95

    target_h = start_hour + (target_minute // 60)
    target_m = target_minute % 60
    if elapsed_minutes < target_minute and not is_last_slot:
        print(
            f"Current BJT {bjt_now.strftime('%H:%M')} < today's random target "
            f"{target_h:02d}:{target_m:02d} BJT for {window_key}, waiting for later slot."
        )
        return False, window_key, bjt_now

    jitter_sec = random.randint(5, 45)
    print(
        f"Reached random target {target_h:02d}:{target_m:02d} BJT for {window_key}; "
        f"sleeping {jitter_sec}s random jitter before sending..."
    )
    time.sleep(jitter_sec)
    bjt_now = datetime.now(BJT)
    return True, window_key, bjt_now


def main():
    state = load_state()
    should_run, window_key, bjt_now = check_schedule_window(state)
    if not should_run:
        return 0

    head_sha = get_head_sha()
    files = sorted({
        path for path in changed_files(state)
        if path.startswith("rss/") and path.endswith(".txt") and os.path.exists(path)
    })
    if not files:
        print("No new RSS TXT files since last send.")
        if window_key:
            state["last_window_key"] = window_key
            state["last_sent_sha"] = head_sha
            save_state(state)
        return 0

    digests = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                digest = parse_digest(file.read(), path)
            if digest.articles:
                digests.append(digest)
            else:
                print(f"Skipping {path}: no RSS articles.")
        except Exception as exc:
            print(f"Skipping {path} due to parse error: {exc}")

    bjt_str = bjt_now.strftime("%Y-%m-%d %H:%M")
    messages = build_combined_email_message(digests, bjt_str)

    if messages:
        send_emails(messages)
        print(f"Sent {len(messages)} combined email(s) covering {len(files)} file(s) to {RECEIVER}.")
    else:
        print("No non-empty RSS articles to email in this window.")

    sent_history = list(dict.fromkeys((state.get("sent_files", []) + files)[-300:]))
    state["last_sent_sha"] = head_sha
    if window_key:
        state["last_window_key"] = window_key
    state["sent_files"] = sent_history
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
