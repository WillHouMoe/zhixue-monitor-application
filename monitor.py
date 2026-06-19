#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import os
import sys
import datetime
import requests
from collections import OrderedDict

# ==================== 从环境变量读取敏感信息（Secrets） ====================

ZHIXUE_XTOKEN = os.getenv("ZHIXUE_XTOKEN")
ZHIXUE_TOKEN = os.getenv("ZHIXUE_TOKEN")
ZHIXUE_AUTHGUID = os.getenv("ZHIXUE_AUTHGUID")
ZHIXUE_AUTHTIMESTAMP = os.getenv("ZHIXUE_AUTHTIMESTAMP")
ZHIXUE_AUTHTOKEN = os.getenv("ZHIXUE_AUTHTOKEN")
SENDKEY = os.getenv("SERVERCHAN_SENDKEY")        # 可为空

required_secrets = [
    ("ZHIXUE_XTOKEN", ZHIXUE_XTOKEN),
    ("ZHIXUE_TOKEN", ZHIXUE_TOKEN),
    ("ZHIXUE_AUTHGUID", ZHIXUE_AUTHGUID),
    ("ZHIXUE_AUTHTIMESTAMP", ZHIXUE_AUTHTIMESTAMP),
    ("ZHIXUE_AUTHTOKEN", ZHIXUE_AUTHTOKEN),
]
missing = [name for name, value in required_secrets if not value]
if missing:
    print(f"❌ 错误：以下必需的 Secrets 未设置：{', '.join(missing)}")
    sys.exit(1)

# ==================== 状态文件路径 ====================
STATE_FILE = "state.json"

# ==================== 工具函数 ====================

def fetch_scores(exam_id, headers):
    """根据 exam_id 和 headers 拉取成绩，返回 OrderedDict"""
    url = f"https://ali-bg.zhixue.com/zhixuebao/report/exam/getReportMain?examId={exam_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"❌ HTTP 错误：{resp.status_code}")
            return None
        text = resp.text.strip()
        if not text:
            print("❌ 响应为空")
            return None
        data = resp.json()
    except Exception as e:
        print(f"❌ 请求或解析失败：{e}")
        return None

    if data.get("errorCode") != 0:
        print(f"❌ 接口返回错误：{data.get('errorInfo', '未知')}")
        return None

    result = data.get("result")
    if not result:
        print("❌ 无 result 数据")
        return None

    paper_list = result.get("paperList", [])
    scores = OrderedDict()
    for paper in paper_list:
        subject = paper.get("subjectName") or paper.get("title", "未知学科")
        user_score = paper.get("userScore")
        standard_score = paper.get("standardScore")
        if subject and user_score is not None and standard_score is not None:
            scores[subject] = (float(user_score), float(standard_score))
    return scores


def format_scores(scores_dict):
    lines = []
    for subject, (user, std) in scores_dict.items():
        lines.append(f"{subject} {user} / {std}")
    return lines


def send_notification(title, content):
    if not SENDKEY:
        print("ℹ️ 未配置 SERVERCHAN_SENDKEY，跳过通知。")
        return False
    api = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    payload = {"title": title, "desp": content}
    try:
        r = requests.post(api, data=payload, timeout=10)
        if r.status_code == 200:
            print(f"✅ 通知发送成功：{title}")
            return True
        else:
            print(f"❌ 通知发送失败，状态码：{r.status_code}")
    except Exception as e:
        print(f"❌ 通知异常：{e}")
    return False


def compare_and_get_changes(previous, current):
    added = []
    modified = []
    removed = []
    for subject, new_scores in current.items():
        if subject not in previous:
            added.append((subject, new_scores))
        else:
            old_scores = previous[subject]
            if old_scores != new_scores:
                modified.append((subject, old_scores[0], old_scores[1], new_scores[0], new_scores[1]))
    for subject, old_scores in previous.items():
        if subject not in current:
            removed.append((subject, old_scores))
    return added, modified, removed


def load_state():
    """读取 state.json，返回 (scores, end_time, exam_id, exam_title)"""
    scores = {}
    end_time = ""
    exam_id = ""
    exam_title = ""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                scores = data.get("scores", {})
                end_time = data.get("end_time", "")
                exam_id = data.get("exam_id", "")
                exam_title = data.get("exam_title", "")
        except:
            pass
    return scores, end_time, exam_id, exam_title


def save_state(scores, end_time, exam_id, exam_title):
    data = {
        "scores": dict(scores),
        "end_time": end_time,
        "exam_id": exam_id,
        "exam_title": exam_title
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==================== 主函数 ====================

def main():
    # ---------- 1. 读取当前状态 ----------
    prev_scores, saved_end_time, saved_exam_id, saved_exam_title = load_state()

    # ---------- 2. 获取本次输入（来自 workflow_dispatch） ----------
    input_exam_id = os.getenv("EXAM_ID", "").strip()
    input_exam_title = os.getenv("EXAM_TITLE", "").strip()
    input_end_time = os.getenv("END_TIME", "").strip()  # 可能为空字符串

    # ---------- 3. 确定最终使用的 exam_id 和 exam_title ----------
    if input_exam_id:
        # 手动运行，明确指定了 exam_id
        exam_id = input_exam_id
        exam_title = input_exam_title if input_exam_title else exam_id
        new_exam_id = exam_id
        new_exam_title = exam_title
    else:
        # 定时任务或手动运行未提供 exam_id，从历史状态读取
        if saved_exam_id:
            exam_id = saved_exam_id
            exam_title = saved_exam_title if saved_exam_title else exam_id
            new_exam_id = saved_exam_id
            new_exam_title = saved_exam_title
        else:
            print("ℹ️ 未提供考试ID，且历史状态中没有考试信息。请手动运行并传入 exam_id。")
            sys.exit(0)   # 正常退出，不触发错误

    # ---------- 4. 处理截止时间 ----------
    # 如果本次输入了 end_time（包括空字符串），则使用它更新；否则保留原值
    if input_end_time is not None:  # 即使为空字符串也视为有输入
        new_end_time = input_end_time
    else:
        new_end_time = saved_end_time

    # 检查是否过期
    if new_end_time:
        try:
            end_dt = datetime.datetime.strptime(new_end_time, "%Y-%m-%d %H:%M:%S")
            now_utc = datetime.datetime.utcnow()
            if now_utc > end_dt:
                print(f"⏰ 当前时间 {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC 已超过截止时间 {new_end_time} UTC")
                print("🛑 监控已自动停止，本次不执行任何操作。")
                # 创建过期标记文件，通知 workflow 删除 work 分支
                with open(".expired", "w") as f:
                    f.write("expired")
                sys.exit(0)
            else:
                print(f"✅ 当前时间 {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC，未到截止时间，继续运行。")
        except ValueError as e:
            print(f"⚠️ 截止时间格式错误（请使用 YYYY-MM-DD HH:MM:SS），已忽略。错误：{e}")

    # ---------- 5. 准备请求头 ----------
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Origin": "https://www.zhixue.com",
        "Pragma": "no-cache",
        "Referer": "https://www.zhixue.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
        "X-Trans-Ready": "true",
        "XToken": ZHIXUE_XTOKEN,
        "authbizcode": "0001",
        "authguid": ZHIXUE_AUTHGUID,
        "authtimestamp": ZHIXUE_AUTHTIMESTAMP,
        "authtoken": ZHIXUE_AUTHTOKEN,
        "role": "student",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "token": ZHIXUE_TOKEN,
    }

    # ---------- 6. 获取当前成绩 ----------
    effective_title = exam_title if exam_title else f"考试({exam_id})"
    print(f"🚀 启动成绩检查：{effective_title}（ID: {exam_id}）")

    current_scores = fetch_scores(exam_id, headers)
    if current_scores is None:
        print("❌ 获取成绩失败，退出。")
        sys.exit(1)

    current_lines = format_scores(current_scores)
    print("\n" + time.strftime("%Y-%m-%d %H:%M:%S") + f" {effective_title} 当前成绩：")
    for line in current_lines:
        print("  " + line)
    print("-" * 40)

    # ---------- 7. 对比变化并发送通知 ----------
    if prev_scores:
        added, modified, removed = compare_and_get_changes(prev_scores, current_scores)
        if added or modified or removed:
            current_scores_str = "\n\n".join(current_lines)
            for subject, (user, std) in added:
                title = f"【新出分】{subject}"
                content = (
                    f"📚 考试：{effective_title}\n"
                    f"📢 科目：{subject}\n"
                    f"得分：{user} / {std}\n\n"
                    f"📋 当前已出分：\n{current_scores_str}"
                )
                send_notification(title, content)
            for subject, old_user, old_std, new_user, new_std in modified:
                title = f"【成绩更新】{subject}"
                if old_std != new_std:
                    score_change = f"{old_user} / {old_std} -> {new_user} / {new_std}"
                else:
                    score_change = f"{old_user} -> {new_user} / {new_std}"
                content = (
                    f"📚 考试：{effective_title}\n"
                    f"📢 科目：{subject}\n"
                    f"变动：{score_change}\n\n"
                    f"📋 当前已出分：\n{current_scores_str}"
                )
                send_notification(title, content)
            for subject, (old_user, old_std) in removed:
                title = f"【成绩撤回】{subject}"
                content = (
                    f"📚 考试：{effective_title}\n"
                    f"📢 科目：{subject}\n"
                    f"原成绩 {old_user} / {old_std} 已消失\n\n"
                    f"📋 当前已出分：\n{current_scores_str}"
                )
                send_notification(title, content)
        else:
            print("✅ 成绩无变化。")
    else:
        # 首次运行（无历史成绩），发送启动通知
        title = f"智学网成绩监控已启动"
        body = "\n\n".join(current_lines) if current_lines else "暂无出分数据"
        content = (
            f"📚 考试：{effective_title}\n"
            f"监控已开始运行，当前出分如下：\n\n{body}"
        )
        send_notification(title, content)
        print("ℹ️ 首次运行，仅发送启动通知。")

    # ---------- 8. 保存状态 ----------
    save_state(current_scores, new_end_time, new_exam_id, new_exam_title)
    print("💾 状态已保存到 state.json。")

    print("✅ 本次执行完成。")


if __name__ == "__main__":
    main()
