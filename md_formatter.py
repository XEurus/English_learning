from email import message
import os
import sys
import re
import time
import argparse
from openai import OpenAI
import concurrent.futures
import threading
from ratelimit import limits, sleep_and_retry

# 全局变量定义
request_counter = 0
total_tokens = 0
prompt_tokens = 0
completion_tokens = 0
has_token_data = False
get_success = 0
max_rps = [120, 60]

def format_markdown(input_file=None, output_file=None, model="bytedance/DeepSeek-V3", max_rps=[120, 60], max_concurrency=20, sys_message=""):
    """
    按照标题（#）分割Markdown文件，使用LLM修正每个部分的格式，然后重新组合
    
    参数:
    input_file: 输入的Markdown文件路径，如果为None则会提示用户输入
    output_file: 输出文件路径，如果为None则会基于输入文件名创建
    model: 使用的模型
    max_rps: 最大请求速率
    max_concurrency: 最大线程池大小
    """
    global request_counter, total_tokens, prompt_tokens, completion_tokens, has_token_data, get_success
    # 重置计数器
    request_counter = 0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    has_token_data = False
    get_success = 0
    
    # 设置API配置
    api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    
    # 初始化OpenAI客户端
    client_kwargs = {"api_key": api_key}
    if api_base:
        client_kwargs["base_url"] = api_base
    
    try:
        client = OpenAI(**client_kwargs)
        print(f"已连接到API服务: {api_base}")
    except Exception as e:
        print(f"初始化API客户端时出错: {e}")
        sys.exit(1)
    
    # 如果未提供输入文件，提示用户输入
    if input_file is None:
        input_file = input("请输入要处理的Markdown文件路径: ").strip()
    
    # 检查文件是否存在
    if not os.path.exists(input_file):
        print(f"文件 '{input_file}' 未找到。")
        return
    
    # 如果未提供输出文件，创建基于输入文件的输出文件
    if output_file is None:
        file_name = os.path.basename(input_file)
        file_dir = os.path.dirname(input_file)
        file_name_without_ext = os.path.splitext(file_name)[0]
        output_file = os.path.join(file_dir, f"{file_name_without_ext}_formatted.md")
    
    # 确保输出文件的目录存在
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    print(f"输出文件: {output_file}")
    
    # 读取Markdown文件
    try:
        with open(input_file, 'r', encoding='utf-8') as file:
            content = file.read()
        print(f"已读取文件: {input_file}")
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return
    
    # 按照标题分割内容
    def split_by_headings(content):
        # 使用正则表达式匹配标题行（以#开头的行）
        heading_pattern = re.compile(r'^(#+\s+.+)$', re.MULTILINE)
        
        # 查找所有标题的位置
        headings = list(heading_pattern.finditer(content))
        
        if not headings:
            # 如果没有标题，将整个内容作为一个部分
            return [content]
        
        # 分割内容
        sections = []
        for i in range(len(headings)):
            start = headings[i].start()
            # 如果是最后一个标题，结束位置是内容的末尾
            end = headings[i+1].start() if i < len(headings) - 1 else len(content)
            sections.append(content[start:end])
        
        return sections
    
    @sleep_and_retry
    @limits(calls=max_rps[0], period=max_rps[1])
    def send_openai_request(client, messages, model, max_retries=3, temperature=0.7, max_tokens=8192):
        """发送OpenAI请求并处理响应"""
        global request_counter, total_tokens, prompt_tokens, completion_tokens, has_token_data, get_success
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                get_success += 1
                
                # 更新token计数
                if hasattr(response, 'usage') and response.usage is not None:
                    with lock:
                        has_token_data = True
                        prompt_tokens += response.usage.prompt_tokens
                        completion_tokens += response.usage.completion_tokens
                        total_tokens += response.usage.total_tokens
                
                return response.choices[0].message.content, response.usage
            except Exception as e:
                print(f"API请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
                time.sleep(2)
        return None, None
    
    # 用于线程安全的锁
    lock = threading.Lock()
    
    # 用于控制并发请求的信号量
    semaphore = threading.Semaphore(max_concurrency)
    
    def format_section(index, section):
        """使用LLM格式化一个部分的内容"""
        with semaphore:
            global request_counter
            with lock:
                request_counter += 1
                current_request = request_counter
            
            print(f"处理部分 {index+1}...")
            
            # 构建提示
            messages = [
                {"role": "system", "content": f"{sys_message}"},
                {"role": "user", "content": f"请修正以下试题的格式：\n\n{section}"}
            ]
            
            # 发送请求
            formatted_section, usage = send_openai_request(client, messages, model)
            
            if formatted_section is None:
                print(f"部分 {index+1} 格式化失败，保留原始内容")
                return index, section
            
            print(f"部分 {index+1} 格式化完成")
            return index, formatted_section
    
    # 分割内容
    sections = split_by_headings(content)
    print(f"文件已分割为 {len(sections)} 个部分")
    
    # 使用线程池并行处理每个部分
    formatted_sections = [None] * len(sections)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        # 提交所有任务
        future_to_index = {executor.submit(format_section, i, section): i for i, section in enumerate(sections)}
        
        # 处理结果
        for future in concurrent.futures.as_completed(future_to_index):
            index, formatted_section = future.result()
            formatted_sections[index] = formatted_section
    
    # 合并格式化后的内容
    formatted_content = '\n'.join(formatted_sections)
    
    # 写入输出文件
    try:
        with open(output_file, 'w', encoding='utf-8') as file:
            file.write(formatted_content)
        print(f"格式化完成，已保存到: {output_file}")
    except Exception as e:
        print(f"写入文件时出错: {e}")
        return
    
    # 打印统计信息
    print("\n处理统计:")
    print(f"总请求数: {request_counter}")
    print(f"成功请求数: {get_success}")
    
    if has_token_data:
        print(f"提示词tokens: {prompt_tokens}")
        print(f"完成tokens: {completion_tokens}")
        print(f"总tokens: {total_tokens}")
    
    return output_file

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Markdown Formatter')
    parser.add_argument('--input_file', type=str, default="test.md",help='Input Markdown file path')
    parser.add_argument('--output_file', type=str, default=None,help='Output Markdown file path')
    parser.add_argument('--model', type=str, default="bytedance/DeepSeek-V3", help='Model to use')
    parser.add_argument('--max_rps', type=int, nargs=2, default=[20000, 60], help='Max requests per second [calls, period]')
    parser.add_argument('--max_concurrency', type=int, default=32, help='Max concurrent requests')
    
    args = parser.parse_args()
    system_message="你是一个专业的试题文档格式化助手。你的任务是整理markdown内容的格式，删去不必要的空行和空格，补全、修正latex公式的格式，加粗题号。只需要输出修正后的markdown内容，不要改变内容的含义。"
    
    format_markdown(
        input_file=args.input_file,
        output_file=args.output_file,
        model=args.model,
        max_rps=args.max_rps,
        max_concurrency=args.max_concurrency,
        sys_message=system_message
    )
