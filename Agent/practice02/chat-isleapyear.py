import configparser
import json
import os
import sys
import subprocess
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = configparser.ConfigParser()
c.read("config.ini", encoding="utf-8")
s = c["llm"]

tools = [{
    "type": "function",
    "function": {
        "name": "getDate",
        "description": "获取当前电脑的日期和时间",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "listFiles",
        "description": "列出指定目录下的所有文件（不包含子目录）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要列出文件的目录路径，例如 C:/temp 或 ."
                }
            },
            "required": ["path"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "isLeapYear",
        "description": "判断输入的年份是否是闰年",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "要判断的年份，例如 2024"
                }
            },
            "required": ["year"]
        }
    }
}]

def getDate():
    result = subprocess.run(
        ["cmd", "/c", "date /t & time /t"],
        capture_output=True, text=True, encoding="gbk", errors="replace",
    )
    return result.stdout.strip()

def listFiles(path):
    if not os.path.isdir(path):
        return f"目录不存在: {path}"
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    return "\n".join(files) if files else f"目录为空: {path}"

def isLeapYear(year):
    # 闰年：能被4整除但不能被100整除，或者能被400整除
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def call_llm(messages):
    r = requests.post(
        s["base_url"],
        headers={"Authorization": f"Bearer {s['api_key']}", "Content-Type": "application/json"},
        json={"model": s["model"], "messages": messages, "tools": tools, "stream": True},
        stream=True,
    )
    content = ""
    tool_calls = []
    for line in r.iter_lines():
        if line.startswith(b"data: ") and line != b"data: [DONE]":
            delta = json.loads(line[6:])["choices"][0]["delta"]
            t = delta.get("content") or ""
            if t:
                content += t
                print(t, end="", flush=True)
            for tc in delta.get("tool_calls") or []:
                i = tc["index"]
                while len(tool_calls) <= i:
                    tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    tool_calls[i]["id"] = tc["id"]
                fn = tc.get("function") or {}
                tool_calls[i]["function"]["name"] += fn.get("name") or ""
                tool_calls[i]["function"]["arguments"] += fn.get("arguments") or ""
    return content, tool_calls

messages = []
while True:
    print("please input your prompt：")
    try:
        prompt = input()
    except EOFError:
        print()
        break
    print("————")

    messages.append({"role": "user", "content": prompt})
    reply = ""
    for _ in range(5):  # at most 5 rounds of tool calls per question
        content, tool_calls = call_llm(messages)
        reply += content
        if not tool_calls:
            break
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "getDate":
                result = getDate()
            elif name == "listFiles":
                result = listFiles(args.get("path", "."))
            elif name == "isLeapYear":
                result = f"{args.get('year')} {'是' if isLeapYear(args.get('year')) else '不是'}闰年"
            else:
                result = f"unknown tool: {name}"
            print(f"\n[tool] {name}({json.dumps(args, ensure_ascii=False)}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    print()
    messages.append({"role": "assistant", "content": reply})
