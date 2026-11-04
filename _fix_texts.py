# -*- coding: utf-8 -*-
"""一次性修复脚本：重写 judge_prompt 块（提及感知+引用认知+拟人语气）。"""
import io

p = "QvQStalker/texts.py"
lines = io.open(p, encoding="utf-8").read().split("\n")

start = next(i for i, l in enumerate(lines) if '"judge_prompt"' in l)
end = start
depth = 0
for i in range(start, len(lines)):
    if '"judge_prompt"' in lines[i] and "{" in lines[i]:
        depth = 1
        continue
    if depth and lines[i].strip() == "},":
        end = i
        break

NL = chr(92) + "n"  # 字面 \n，出现在字符串内部
new_block_lines = [
    '    "judge_prompt": {',
    '        "zh-CN": (',
    '            "以下是群聊最近的旁听记录（每行一条，格式 昵称(id): 内容）:' + NL + '{window}' + NL + NL + '"',
    '            "当前消息：{sender}: {text}' + NL + '{mention_note}' + NL + '"',
    '            "你是这个群里的普通成员（一个机器人），这条消息没有点你的名，你在考虑要不要像群友一样自然地搭句话。"',
    '            "判断时注意：' + NL + '"',
    '            "1. 先分辨对方是在跟别人聊话题，还是在向你提问。只有当你能给出对方真正需要的回应（答案、补充、共情）时才值得插话；插不上话就保持安静，安静是常态。' + NL + '"',
    '            "2. 消息若引用回复了某条旧消息：话题几乎总是关于被引用的那条内容，去旁听记录里找到那条内容来理解问题即可，不存在「不知道在问哪条」的情况。' + NL + '"',
    '            "3. 决定插话时，语气要像群友随口接话：口语化、简短、有来有回；不要书面腔，不要长篇大论。' + NL + NL + '"',
    '            "请判断此刻插话是否自然、有价值。只回答「是」或「否」，不要输出任何其他内容。"',
    '        ),',
    '        "en": (',
    '            "Recent group messages overheard (one per line, format name(id): text):' + NL + '{window}' + NL + NL + '"',
    '            "Current message: {sender}: {text}' + NL + '{mention_note}' + NL + '"',
    '            "You are an ordinary member of this group (a bot). This message does not address you; you are deciding whether to join in naturally, like a fellow group member. Keep in mind:' + NL + '"',
    '            "1. First tell whether the sender is chatting with others or asking you. Speak up only when you can give the response they actually need (an answer, a supplement, empathy); staying silent is the norm.' + NL + '"',
    '            "2. If the message quote-replies to an older one, the topic is almost always about the quoted content - find it in the overheard log; there is no unrecorded-message confusion." + NL + "',
    '            "3. If you do join in, talk like a casual group member: colloquial, brief, conversational; no formal tone, no walls of text.' + NL + NL + '"',
    '            "Decide whether joining now feels natural and useful. Answer with exactly YES or NO."',
    '        ),',
    '    },',
    '    "mention_note_direct": {',
    '        "zh-CN": "（注意：这条消息 @ 了你，是在向你提问或点名，回复它就是你分内的事。）",',
    '        "en": "(Note: this message mentions you - answering it is your job.)",',
    '    },',
    '    "mention_note_none": {',
    '        "zh-CN": "（注意：这条消息没有提到你，你只是旁听后考虑要不要主动搭话。）",',
    '        "en": "(Note: this message does not mention you; you are merely considering jumping in after overhearing.)",',
    '    },',
]

lines[start:end + 1] = new_block_lines
io.open(p, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("judge_prompt rewritten at", start, "->", start + len(new_block_lines) - 1)
