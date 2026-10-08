import unittest
from email import message_from_string
from unittest.mock import patch

import notify_rss
from notify_rss import build_email_messages, chunk_articles, parse_digest


def make_article(number):
    return "\n".join([
        f"{number}. 示例新闻标题{number}",
        "【相关股票】示例公司A、示例公司B",
        "【利好股票】示例公司A",
        "【利空股票】暂未判断",
        f"【摘要】新闻正文第{number}段第一句。",
        f"新闻正文第{number}段第二句，需完整保留。",
        "【依据】示例依据。",
        f"【原标题】Original title {number}",
        f"【发布时间】2026-10-08T10:{number:02d}:00Z",
        f"【链接】https://example.com/news/{number}",
    ])


def make_digest_text(count):
    header = [
        "示例RSS - 中文结构化新闻汇总",
        "",
        "【来源】示例RSS",
        "【时间】2026-10-08T08:00:00Z - 2026-10-08T10:00:00Z",
        f"【数量】{count}",
        "",
        "【本邮件新闻标题汇总】",
    ]
    header.extend(f"{i}. 示例新闻标题{i}" for i in range(1, count + 1))
    article_text = "\n\n------------------------------\n\n".join(
        make_article(i) for i in range(1, count + 1)
    )
    return "\n".join(header) + "\n\n==============================\n\n" + article_text


class NotifyRssTests(unittest.TestCase):
    def test_34_articles_are_split_into_8_8_8_8_2(self):
        digest = parse_digest(make_digest_text(34))

        self.assertEqual([len(batch) for batch in chunk_articles(digest.articles)], [8, 8, 8, 8, 2])
        messages = build_email_messages(digest)
        self.assertEqual(len(messages), 5)
        self.assertIn("第1/5封", messages[0][0])
        self.assertIn("2条新闻", messages[-1][0])
        self.assertIn("新闻33–34", messages[-1][1])

    def test_29_articles_are_split_into_8_8_8_5(self):
        digest = parse_digest(make_digest_text(29))

        self.assertEqual([len(batch) for batch in chunk_articles(digest.articles)], [8, 8, 8, 5])
        self.assertEqual(len(build_email_messages(digest)), 4)

    def test_one_article_needs_no_between_article_separator(self):
        digest = parse_digest(make_digest_text(1))

        self.assertEqual(len(digest.articles), 1)
        self.assertEqual(len(build_email_messages(digest)), 1)

    def test_zero_article_digest_produces_no_email(self):
        digest = parse_digest(make_digest_text(0))

        self.assertEqual(digest.articles, [])
        self.assertEqual(build_email_messages(digest), [])

    def test_full_news_body_is_preserved_and_labeled(self):
        digest = parse_digest(make_digest_text(1))
        _, body = build_email_messages(digest)[0]

        self.assertIn("【新闻正文】新闻正文第1段第一句。", body)
        self.assertIn("新闻正文第1段第二句，需完整保留。", body)
        self.assertNotIn("【摘要】", body)
        self.assertIn("【原标题】Original title 1", body)

    def test_each_email_contains_only_its_batch_headlines(self):
        digest = parse_digest(make_digest_text(9))
        messages = build_email_messages(digest)

        self.assertEqual(len(messages), 2)
        self.assertIn("1. 示例新闻标题1", messages[0][1])
        self.assertNotIn("示例新闻标题9", messages[0][1])
        self.assertIn("1. 示例新闻标题9", messages[1][1])

    def test_declared_article_count_must_match_parsed_count(self):
        text = make_digest_text(1).replace("【数量】1", "【数量】2")

        with self.assertRaisesRegex(ValueError, "header says 2 articles, but parsed 1"):
            parse_digest(text)

    @patch("notify_rss.smtplib.SMTP_SSL")
    def test_batch_emails_are_sent_as_plain_text_in_one_session(self, smtp_ssl):
        sender = "sender@example.com"
        receiver = "receiver@example.com"
        server = smtp_ssl.return_value.__enter__.return_value
        messages = [("subject 1", "body 1"), ("subject 2", "body 2")]

        with patch.object(notify_rss, "MAIL_USER", sender), \
                patch.object(notify_rss, "MAIL_PASS", "test-password"), \
                patch.object(notify_rss, "RECEIVER", receiver):
            notify_rss.send_emails(messages)

        smtp_ssl.assert_called_once_with("smtp.qq.com", 465)
        server.login.assert_called_once_with(sender, "test-password")
        self.assertEqual(server.sendmail.call_count, 2)
        for call, expected_body in zip(server.sendmail.call_args_list, ("body 1", "body 2")):
            self.assertEqual(call.args[0], sender)
            self.assertEqual(call.args[1], [receiver])
            message = message_from_string(call.args[2])
            self.assertEqual(message.get_content_type(), "text/plain")
            self.assertIn(expected_body, message.get_payload(decode=True).decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
