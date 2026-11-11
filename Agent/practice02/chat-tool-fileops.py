import configparser
import json
import mimetypes
import os
import shutil
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
}, {
    "type": "function",
    "function": {
        "name": "getCwd",
        "description": "获取当前工作目录",
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
        "name": "getFileType",
        "description": "获取指定文件的类型（扩展名和MIME类型）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt"
                }
            },
            "required": ["path"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "readFile",
        "description": "获取指定文件的内容（仅支持纯文本格式文件）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt"
                }
            },
            "required": ["path"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "createFile",
        "description": "根据指定的目录和文件名创建新文件",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "文件所在目录，例如 C:/temp"
                },
                "filename": {
                    "type": "string",
                    "description": "文件名，例如 test.txt"
                }
            },
            "required": ["directory", "filename"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "editFile",
        "description": "编辑指定文件的内容（仅支持纯文本格式文件），默认覆盖原内容，可选追加",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，例如 C:/temp/test.txt"
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的内容"
                },
                "append": {
                    "type": "boolean",
                    "description": "是否追加到文件末尾，默认 false 表示覆盖"
                }
            },
            "required": ["path", "content"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "mkdir",
        "description": "创建新目录（包括多级目录）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "目录路径，例如 C:/temp/newdir"
                }
            },
            "required": ["path"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "removeDir",
        "description": "删除目录（非空目录也会连同其中所有内容一起删除）",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要删除的目录路径，例如 C:/temp/newdir"
                }
            },
            "required": ["path"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "removeFile",
        "description": "删除指定的文件",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要删除的文件路径，例如 C:/temp/test.txt"
                }
            },
            "required": ["path"]
        }
    }
}]

def getDate():
    result = subprocess.run(
        ["cmd", "/c", "date /t & time /t"],
        capture_output=True, text=True, encoding="gbk", errors="replace",
    )
    return result.stdout.strip()

def isLeapYear(year):
    # 闰年：能被4整除但不能被100整除，或者能被400整除
    year = int(year)
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def getCwd():
    return os.getcwd()

def listFiles(path):
    if not os.path.isdir(path):
        return f"目录不存在: {path}"
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    return "\n".join(files) if files else f"目录为空: {path}"

def getFileType(path):
    if not os.path.exists(path):
        return f"文件不存在: {path}"
    if os.path.isdir(path):
        return f"{path} 是一个目录"
    ext = os.path.splitext(path)[1] or "(无扩展名)"
    mime, _ = mimetypes.guess_type(path)
    return f"扩展名: {ext}, MIME类型: {mime or '未知'}"

def readFile(path):
    if not os.path.isfile(path):
        return f"文件不存在: {path}"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(50000)
    if len(content) == 50000:
        content += "\n...(内容过长，已截断)"
    return content

def createFile(directory, filename):
    path = os.path.join(directory, filename)
    if os.path.exists(path):
        return f"文件已存在: {path}"
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass
    return f"已创建文件: {path}"

def editFile(path, content, append=False):
    if not os.path.isfile(path):
        return f"文件不存在: {path}"
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as f:
        f.write(content)
    return f"已{'追加' if append else '覆盖'}写入: {path}"

def mkdir(path):
    if os.path.isdir(path):
        return f"目录已存在: {path}"
    os.makedirs(path)
    return f"已创建目录: {path}"

def removeDir(path):
    if not os.path.isdir(path):
        return f"目录不存在: {path}"
    shutil.rmtree(path)
    return f"已删除目录(含其中所有内容): {path}"

def removeFile(path):
    if not os.path.isfile(path):
        return f"文件不存在: {path}"
    os.remove(path)
    return f"已删除文件: {path}"

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

def run_tool(name, args):
    if name == "getDate":
        return getDate()
    if name == "isLeapYear":
        year = args.get("year")
        return f"{year} {'是' if isLeapYear(year) else '不是'}闰年"
    if name == "getCwd":
        return getCwd()
    if name == "listFiles":
        return listFiles(args.get("path", "."))
    if name == "getFileType":
        return getFileType(args.get("path", ""))
    if name == "readFile":
        return readFile(args.get("path", ""))
    if name == "createFile":
        return createFile(args.get("directory", "."), args.get("filename", ""))
    if name == "editFile":
        return editFile(args.get("path", ""), args.get("content", ""), args.get("append", False))
    if name == "mkdir":
        return mkdir(args.get("path", ""))
    if name == "removeDir":
        return removeDir(args.get("path", ""))
    if name == "removeFile":
        return removeFile(args.get("path", ""))
    return f"unknown tool: {name}"

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
            result = run_tool(name, args)
            print(f"\n[tool] {name}({json.dumps(args, ensure_ascii=False)}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    print()
    messages.append({"role": "assistant", "content": reply})
