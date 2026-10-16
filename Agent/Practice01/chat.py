import configparser
import json
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = configparser.ConfigParser()
c.read("config.ini", encoding="utf-8")
s = c["llm"]

messages = []
while True:
    print("please input your prompt：")
    prompt = input()
    print("————")

    messages.append({"role": "user", "content": prompt})
    r = requests.post(
        s["base_url"],
        headers={"Authorization": f"Bearer {s['api_key']}", "Content-Type": "application/json"},
        json={"model": s["model"], "messages": messages, "stream": True},
        stream=True,
    )
    reply = ""
    for line in r.iter_lines():
        if line.startswith(b"data: ") and line != b"data: [DONE]":
            t = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
            reply += t
            print(t, end="", flush=True)
    print()
    messages.append({"role": "assistant", "content": reply})
