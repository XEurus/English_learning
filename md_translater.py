import os
import sys
import datetime
import re
import time
from openai import OpenAI
import concurrent.futures
import threading
from ratelimit import limits, sleep_and_retry
import argparse

# 全局变量定义
request_counter = 0
total_tokens = 0
prompt_tokens = 0
completion_tokens = 0
has_token_data = False
get_success = 0
max_rps = [120, 60]

def translate_paper(input_file=None, output_dir=None, model="abab6.5s-chat", max_rps=[120, 60], max_concurrency=20,api_key=None,api_base="http://127.0.0.1:33019/v1", progress_callback=None, exit_on_error=True):
    """
    翻译Markdown文件，生成中文和双语版本，按行分割逐行对照翻译
    
    参数:
    input_file: 输入的Markdown文件路径，如果为None则会提示用户输入
    output_dir: 输出目录，如果为None则会创建基于时间戳的目录
    model: 使用的模型
    max_rps: 最大请求速率
    max_concurrency: 最大线程池大小
    """
    global request_counter, total_tokens, prompt_tokens, completion_tokens, has_token_data,get_success
    # 重置计数器
    request_counter = 0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    has_token_data = False
    
    # 初始化OpenAI客户端
    client_kwargs = {"api_key": api_key}
    if api_base:
        client_kwargs["base_url"] = api_base
    
    try:
        client = OpenAI(**client_kwargs)
        print(f"已连接到API服务: {api_base}")
    except Exception as e:
        print(f"初始化API客户端时出错: {e}")
        if exit_on_error:
            sys.exit(1)
        return False
    
    # 如果未提供输入文件，提示用户输入
    if input_file is None:
        input_file = input("请输入要翻译的Markdown文件路径: ").strip()
    
    # 检查文件是否存在
    if not os.path.exists(input_file):
        print(f"文件 '{input_file}' 未找到。")
        return
    
    # 如果未提供输出目录，创建基于时间戳的输出目录
    if output_dir is None:
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = f"translation_{timestamp}"
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    print(f"输出目录: {output_dir}")
    
    # 设置输出文件路径
    file_name = os.path.basename(input_file)
    file_name_without_ext = os.path.splitext(file_name)[0]
    
    chinese_file = os.path.join(output_dir, f"{file_name_without_ext}_zh.md")
    bilingual_file = os.path.join(output_dir, f"{file_name_without_ext}_bilingual.md")
    
    # 读取Markdown文件
    try:
        with open(input_file, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        print(f"已读取文件: {input_file}，共 {len(lines)} 行")
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return
    
    # 预处理行，合并代码块
    def preprocess_lines(lines):
        processed_lines = []
        i = 0
        in_code_block = False
        current_code_block = []
        in_math_block = False
        current_math_block = []
        
        while i < len(lines):
            line = lines[i].rstrip()
            
            # 检测数学公式块开始 ($$)
            if line.strip() == '$$' and not in_code_block and not in_math_block:
                in_math_block = True
                current_math_block = [line]
                i += 1
                continue
            
            # 在数学公式块内
            if in_math_block:
                current_math_block.append(line)
                # 检测数学公式块结束
                if line.strip() == '$$':
                    processed_lines.append('\n'.join(current_math_block))
                    current_math_block = []
                    in_math_block = False
                i += 1
                continue
            
            # 检测代码块开始
            if re.match(r'^```(\w*)$', line) and not in_code_block and not in_math_block:
                in_code_block = True
                current_code_block = [line]
                i += 1
                continue
            
            # 在代码块内
            if in_code_block:
                current_code_block.append(line)
                # 检测代码块结束
                if line.strip() == '```':
                    processed_lines.append('\n'.join(current_code_block))
                    current_code_block = []
                    in_code_block = False
                i += 1
                continue
            
            # 普通行
            processed_lines.append(line)
            i += 1
        
        # 处理最后可能未闭合的代码块或数学公式块
        if current_code_block:
            processed_lines.append('\n'.join(current_code_block))
        if current_math_block:
            processed_lines.append('\n'.join(current_math_block))
        
        return processed_lines
    
    @sleep_and_retry
    @limits(calls=max_rps[0], period=max_rps[1])
    def send_openai_request(client, messages, model, max_retries=1,temperature=0.7,max_tokens=8192):
        """发送OpenAI请求并处理响应"""
        global request_counter, total_tokens, prompt_tokens, completion_tokens, has_token_data,get_success
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                get_success += 1
                return response.choices[0].message.content, response.usage
            except Exception as e:
                print(f"API请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
                time.sleep(2)
        return None, None
    # 预处理行
    processed_lines = preprocess_lines(lines)
    total_lines = len(processed_lines)
    print(f"预处理后共 {total_lines} 行")
    if progress_callback:
        try:
            progress_callback(0, total_lines)
        except Exception:
            pass
    
    # 用于存储翻译结果的字典
    translations = {}
    
    # 用于线程安全的锁
    lock = threading.Lock()
    
    # 用于控制并发请求的信号量
    semaphore = threading.Semaphore(max_concurrency)
    
    def translate_line(index, line):
        global request_counter, total_tokens, prompt_tokens, completion_tokens, has_token_data
        # 跳过空行或只有空白字符的行
        if not line.strip():
            return index, line
        
        # 检查是否为不需要翻译的内容（代码块、表格、图片等）
        if line.startswith('```') or '```' in line:
            return index, line
        
        # 检查是否为数学公式块
        if line.strip() == '$$' or (line.startswith('$$') and line.endswith('$$')):
            return index, line
            
        # 检查是否为表格
        if re.match(r'^\s*\|.*\|\s*$', line):
            return index, line
        
        # 检查是否为图片或链接
        if re.match(r'^\s*!\[.*\]\(.*\)\s*$', line) or re.match(r'^\s*\[.*\]\(.*\)\s*$', line):
            return index, line
        
        # 检查是否只包含标点符号或特殊字符
        if re.match(r'^\s*[^\w\s]*\s*$', line):
            return index, line
            
        with semaphore:
            messages = [
                {
                    "role": "system",
                    "content": """
# 智能Markdown翻译引擎协议
你是一个专业的英译中翻译器，专门用于逐行翻译Markdown文档。
## 翻译规则：
1. 保持Markdown格式不变，包括标题、列表、强调等
2. 代码块、图片链接、数学公式等内容不需要翻译
3. 将中文翻译成流畅、自然的英文
## 输出要求：
- 只返回翻译结果，不要输出额外内容"""
                },
                {
                    "role": "user",
                    "content": f"{line}"
                }
            ]            
            try:
                with lock:
                    request_counter += 1
                translation, usage = send_openai_request(client, messages, model, max_retries=10, temperature=0.3, max_tokens=4096)
                # 如果翻译结果为None，则返回原文
                if translation is None:
                    with lock:
                        print(f"翻译第 {index+1} 行失败，使用原文: {line[:30]}...")
                    return index, line
                
                if usage:
                    with lock:
                        has_token_data = True
                        total_tokens += usage.total_tokens
                        prompt_tokens += usage.prompt_tokens
                        completion_tokens += usage.completion_tokens
                
                with lock:
                    print(f"已翻译 {index+1}/{len(processed_lines)} 行")
                return index, translation
            except Exception as e:
                with lock:
                    print(f"翻译第 {index+1} 行时出错: {str(e)}")
                return index, line  # 返回原文而不是错误信息
    
    # 使用线程池并发翻译
    print(f"开始并发翻译，最大并发数: {max_concurrency}")
    completed_lines = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        future_to_index = {executor.submit(translate_line, i, line): i for i, line in enumerate(processed_lines)}
        
        for future in concurrent.futures.as_completed(future_to_index):
            index, translation = future.result()
            translations[index] = translation
            completed_lines += 1
            if progress_callback:
                try:
                    progress_callback(completed_lines, total_lines)
                except Exception:
                    pass
    
    # 按原始顺序组织翻译结果
    ordered_translations = [translations.get(i, "") for i in range(len(processed_lines))]
    
    # 生成纯中文文档
    try:
        with open(chinese_file, 'w', encoding='utf-8') as file:
            for translation in ordered_translations:
                file.write(translation + "\n")
        print(f"纯中文文档已保存到 {chinese_file}")
    except Exception as e:
        print(f"保存纯中文文档时出错: {e}")
    
    # 生成中英对照文档
    try:
        with open(bilingual_file, 'w', encoding='utf-8') as file:
            for i, (line, translation) in enumerate(zip(processed_lines, ordered_translations)):
                # 跳过空行
                if not line.strip():
                    file.write("\n")
                    continue
                
                # 对于代码块、表格、图片等特殊元素，不添加中文翻译
                if line == translation:
                    file.write(line + "\n")
                    file.write("\n")  # 换行
                else:
                    file.write(line + "\n\n")
                    file.write(translation + "\n\n")
        print(f"中英对照文档已保存到 {bilingual_file}")
    except Exception as e:
        print(f"保存中英对照文档时出错: {e}")
    
    print(f"\n=== 翻译统计 ===")
    print(f"总处理行数: {total_lines}")
    print(f"总API请求次数: {request_counter},成功次数: {get_success}")
    if request_counter > 0:
        print(f"请求成功率: {get_success/request_counter*100:.2f}%")
    else:
        print(f"请求成功率: 0.00%")
    
    if has_token_data and total_tokens > 0:
        print(f"\n=== Token使用统计 ===")
        print(f"总Token数: {total_tokens}")
        print(f"Prompt Tokens: {prompt_tokens} ({prompt_tokens/total_tokens:.1%})")
        print(f"Completion Tokens: {completion_tokens} ({completion_tokens/total_tokens:.1%})")
        print(f"估算成本: {(prompt_tokens/1000000*2 + completion_tokens/1000000*8):.4f}元 (2元-8元/百万token)")
    
    print(f"\n=== 翻译完成 ===")

    return True
if __name__ == "__main__":
    from config import (
        DEFAULT_INPUT_FILE,
        DEFAULT_OUTPUT_DIR,
        DEFAULT_MODEL,
        DEFAULT_MAX_RPS,
        DEFAULT_MAX_CONCURRENCY,
        DEFAULT_API_KEY,
        DEFAULT_API_BASE,
    )

    parser = argparse.ArgumentParser(description='Markdown Translator')
    parser.add_argument('--input_file', type=str, default=DEFAULT_INPUT_FILE, help='Input Markdown file path')
    parser.add_argument('--output_dir', type=str, default=DEFAULT_OUTPUT_DIR, help='Output directory')
    parser.add_argument('--model', type=str, default=DEFAULT_MODEL, help='Model to use')
    parser.add_argument('--max_rps', type=int, nargs=2, default=DEFAULT_MAX_RPS, help='Maximum requests per second [calls, period]')
    parser.add_argument('--max_concurrency', type=int, default=DEFAULT_MAX_CONCURRENCY, help='Maximum number of concurrent threads')
    args = parser.parse_args()
    # abab6.5s-chat https://api.siliconflow.cn/v1/chat/completions
    # Doubao1.5-32k
    # bytedance/DeepSeek-V3
    # 设置API配置
    api_key = DEFAULT_API_KEY  # 请替换为您的API密钥
    api_base = DEFAULT_API_BASE  # 请替换为您的API基础URL
    translate_paper(
        input_file=args.input_file,
        output_dir=args.output_dir,
        model=args.model,
        max_rps=args.max_rps,
        max_concurrency=args.max_concurrency,
        api_key=api_key,
        api_base=api_base,
    )