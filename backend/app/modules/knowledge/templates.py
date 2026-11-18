"""The two first-party templates, expressed only as registered capabilities.

There is no "paper reading" branch anywhere in the executor or the adapters: a paper template
is a list of ordinary capability calls, exactly like a learning plan. That is the point of the
second flow — it proves the kernel composes rather than accumulating special cases.

A person can copy either template into their own, change the minutes and the follow-up, and
save it; the saved copy is an ordinary `ai_templates` row.
"""
import json
from uuid import uuid4

from ...core.database import connection, utc_now


def learning_plan(daily_minutes=40, session_minutes=25):
    """Create a node with a testable finish line, then a practice task for it."""
    return [
        {'capability_id': 'learning.node.create',
         'arguments': {'path_id': '$path', 'title': '本周主题',
                       'acceptance_criteria': '能用自己的话解释核心概念并举一个例子'}},
        {'capability_id': 'learning.practice.create',
         'arguments': {'node_id': '$node0', 'title': f'{session_minutes} 分钟专注练习'}},
        {'capability_id': 'notes.create',
         'arguments': {'title': '本周学习记录', 'body': '记下理解、疑问与下一步。'}},
    ]


def paper_reading(session_minutes=25, review_after_days=3):
    """Read one paper: note the questions first, then one practice task per reading block."""
    return [
        {'capability_id': 'notes.create',
         'arguments': {'title': '精读问题清单',
                       'body': '读之前先写下：作者要解决什么问题？方法的关键假设是什么？证据是否支持结论？'}},
        {'capability_id': 'learning.node.create',
         'arguments': {'path_id': '$path', 'title': f'精读一篇论文（{session_minutes} 分钟一轮）',
                       'acceptance_criteria': '能复述方法与结论，并指出至少一处局限'}},
        {'capability_id': 'learning.practice.create',
         'arguments': {'node_id': '$node0',
                       'title': f'第 1 轮精读（{session_minutes} 分钟）'}},
        {'capability_id': 'learning.practice.create',
         'arguments': {'node_id': '$node0',
                       'title': f'{review_after_days} 天后复习一遍'}},
        {'capability_id': 'notes.create',
         'arguments': {'title': '论文精读笔记', 'body': '方法、证据、局限，以及我要怎么用。'}},
    ]


def ensure_default_templates(database):
    """Insert the two starting templates once; never overwrite a person's edits."""
    with connection(database) as con:
        existing = {row[0] for row in con.exec_driver_sql('SELECT name FROM ai_templates').fetchall()}
    made = []
    for name, purpose, steps in (
        ('学习推进（默认）', '为一个学习目标安排一个知识点、一次练习和一份记录。', learning_plan()),
        ('论文精读（默认）', '先列精读问题，再读、再复习，最后写一条精读笔记。', paper_reading()),
    ):
        if name in existing:
            continue
        now = utc_now()
        with connection(database, write=True) as con:
            tid = str(uuid4())
            con.exec_driver_sql(
                "INSERT INTO ai_templates(id,name,purpose,version,enabled,steps_json,created_at,updated_at) "
                "VALUES(?,?,?,1,1,?,?,?)",
                (tid, name, purpose, json.dumps(steps, ensure_ascii=False), now, now))
        made.append(tid)
    return made
