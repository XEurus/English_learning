import os
import sys
import re
import json
import time
import argparse
from openai import OpenAI
import concurrent.futures
import threading
from ratelimit import limits, sleep_and_retry
from concurrent.futures import ThreadPoolExecutor

# 全局变量定义
request_counter = 0
total_tokens = 0
prompt_tokens = 0
completion_tokens = 0
has_token_data = False
get_success = 0
max_rps = [120, 60]

def extract_terms_and_formulas(input_file=None, output_dir=None, model="bytedance/DeepSeek-V3", max_rps=[120, 60], max_concurrency=20):
    """
    从Markdown格式的论文中提取专业名词和数学公式，并使用LLM解释这些内容
    
    参数:
    input_file: 输入的Markdown文件路径，如果为None则会提示用户输入
    output_dir: 输出目录，如果为None则会基于时间戳创建
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
    
    # 如果未提供输出目录，创建基于输入文件的输出目录
    if output_dir is None:
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = os.path.basename(input_file)
        file_name_without_ext = os.path.splitext(file_name)[0]
        output_dir = f"term_extraction_{file_name_without_ext}_{timestamp}"
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    print(f"输出目录: {output_dir}")
    
    # 设置输出文件路径
    terms_file = os.path.join(output_dir, "extracted_terms.json")
    explanations_file = os.path.join(output_dir, "term_explanations.md")
    section_explanations_file = os.path.join(output_dir, "section_explanations.md")
    
    # 读取Markdown文件
    try:
        with open(input_file, 'r', encoding='utf-8') as file:
            content = file.read()
        print(f"已读取文件: {input_file}")
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return
    
    # 用于线程安全的锁
    lock = threading.Lock()
    
    # 用于控制并发请求的信号量
    semaphore = threading.Semaphore(max_concurrency)
    
    def split_by_headings(content):
        """按照标题分割内容"""
        # 使用正则表达式匹配标题行（以#开头的行）
        heading_pattern = re.compile(r'^(#+\s+.+)$', re.MULTILINE)
        
        # 查找所有标题的位置
        headings = list(heading_pattern.finditer(content))
        
        if not headings:
            # 如果没有标题，将整个内容作为一个部分
            return [{"title": "全文", "content": content}]
        
        # 分割内容
        sections = []
        for i in range(len(headings)):
            start = headings[i].start()
            # 如果是最后一个标题，结束位置是内容的末尾
            end = headings[i+1].start() if i < len(headings) - 1 else len(content)
            section_content = content[start:end]
            
            # 获取标题
            title_match = heading_pattern.search(section_content)
            title = title_match.group(1) if title_match else f"部分 {i+1}"
            
            sections.append({"title": title, "content": section_content})
        
        return sections
    
    def extract_formulas(text):
        """提取文本中的数学公式"""
        # 提取行内公式 $...$
        inline_formulas = re.findall(r'\$([^$]+?)\$', text)
        
        # 提取块级公式 $$...$$
        block_formulas = re.findall(r'\$\$([\s\S]+?)\$\$', text)
        
        return {"inline": inline_formulas, "block": block_formulas}
    
    @sleep_and_retry
    @limits(calls=max_rps[0], period=max_rps[1])
    def send_openai_request(client, messages, model, max_retries=3, temperature=0.7, max_tokens=4096):
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
    
    # 按照标题分割内容
    sections = split_by_headings(content)
    print(f"文件已分割为 {len(sections)} 个部分")
    
    def extract_terms_from_section(section_data):
        """使用LLM从一个部分中提取专业名词和公式"""
        index, section = section_data
        with semaphore:
            print(f"正在处理部分 {index+1}/{len(sections)}: {section['title']}")
            
            # 提取公式
            formulas = extract_formulas(section['content'])
            
            # 使用LLM提取专业术语
            system_prompt = """
            你是一个专业的学术术语提取助手。你的任务是从学术论文的一个章节中提取所有专业术语和概念，并将它们分类。
            专业术语包括但不限于：
            1. 特定领域的专业词汇
            2. 算法名称
            3. 模型名称
            4. 方法名称
            5. 技术名称
            
            请以JSON格式返回提取结果，包含以下字段：
            {
                "terms": [
                    {
                        "term": "术语名称",
                        "category": "术语类别" // 如'算法', '模型', '方法', '技术', '概念'等
                    },
                    ...
                ]
            }
            
            请确保提取出所有重要的专业术语，不要漏掉任何一个。同时，避免提取一些基础通用词汇。
            仅返回符合要求的JSON格式数据，不要包含任何其他说明文字。
            """
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"以下是学术论文的一个章节，请提取其中的专业术语:\n\n{section['content']}"}
            ]
            
            response_text, usage = send_openai_request(client, messages, model)
            
            if response_text:
                try:
                    # 尝试解析JSON
                    # 查找回复中的JSON部分
                    json_match = re.search(r'```json\s*([\s\S]+?)\s*```|({[\s\S]+})', response_text)
                    
                    if json_match:
                        json_str = json_match.group(1) if json_match.group(1) else json_match.group(2)
                        terms_data = json.loads(json_str)
                    else:
                        terms_data = json.loads(response_text)
                    
                    with lock:
                        section_result = {
                            "title": section['title'],
                            "terms": terms_data.get("terms", []),
                            "formulas": formulas
                        }
                        return section_result
                except json.JSONDecodeError as e:
                    print(f"解析部分 {index+1} 的JSON响应时出错: {e}")
                    print(f"原始响应: {response_text[:200]}...")
            
            return {
                "title": section['title'],
                "terms": [],
                "formulas": formulas
            }
    
    def deduplicate_terms_and_formulas(all_sections):
        """使用LLM合并并去除重复的术语和公式"""
        print("正在合并并去除重复术语...")
        
        # 提取所有术语和公式
        all_terms = []
        all_formulas = {"inline": [], "block": []}
        
        for section in all_sections:
            # 添加术语
            all_terms.extend(section.get("terms", []))
            
            # 添加公式
            formulas = section.get("formulas", {})
            all_formulas["inline"].extend(formulas.get("inline", []))
            all_formulas["block"].extend(formulas.get("block", []))
        
        # 将所有术语和公式转换为JSON字符串
        input_json = json.dumps({
            "terms": all_terms,
            "formulas": all_formulas
        }, ensure_ascii=False, indent=2)
        
        system_prompt = """
        你是一个学术术语整理专家。你的任务是对提取出的学术术语和数学公式进行去重和整理。
        请遵循以下规则：
        1. 删除完全重复的术语或公式
        2. 合并非常相似的术语，保留更正式或更完整的表达
        3. 对于数学公式，只保留唯一的公式，删除完全相同的公式
        4. 确保返回的结果是有效的JSON格式
        
        请以JSON格式返回结果，格式如下：
        {
            "terms": [
                {
                    "term": "术语名称",
                    "category": "术语类别"
                },
                ...
            ],
            "formulas": {
                "inline": ["公式1", "公式2", ...],
                "block": ["块级公式1", "块级公式2", ...]
            }
        }
        
        仅返回符合要求的JSON格式数据，不要包含任何其他说明文字。
        """
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是从学术论文中提取的术语和公式，请进行去重和整理:\n\n{input_json}"}
        ]
        
        response_text, usage = send_openai_request(client, messages, model)
        
        if response_text:
            try:
                # 尝试解析JSON
                json_match = re.search(r'```json\s*([\s\S]+?)\s*```|({[\s\S]+})', response_text)
                
                if json_match:
                    json_str = json_match.group(1) if json_match.group(1) else json_match.group(2)
                    unique_data = json.loads(json_str)
                else:
                    unique_data = json.loads(response_text)
                
                return unique_data
            except json.JSONDecodeError as e:
                print(f"解析去重响应时出错: {e}")
                print(f"原始响应: {response_text[:200]}...")
        
        # 如果处理失败，返回原始数据
        return {
            "terms": all_terms,
            "formulas": all_formulas
        }
    
    def generate_term_explanation(term_data):
        """生成术语解释"""
        term, category = term_data
        with semaphore:
            print(f"正在解释术语: {term}")
            
            system_prompt = """
            你是一个专业的学术解释助手。你的任务是解释学术论文中的专业术语，使其易于理解。
            
            请遵循以下指导：
            1. 给出术语的清晰定义
            2. 解释术语在相关领域的重要性
            3. 如果适用，提供一些简单的例子
            4. 避免使用过于技术性的语言，使解释对初学者友好
            5. 简洁明了，每个解释不超过150词
            
            以Markdown格式返回解释。
            """
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请解释以下{category}术语: {term}"}
            ]
            
            explanation, usage = send_openai_request(client, messages, model)
            
            return {
                "term": term,
                "category": category,
                "explanation": explanation or "无法获取解释"
            }
    
    def generate_formula_explanation(formula_data):
        """生成公式解释"""
        formula_type, formula = formula_data
        with semaphore:
            print(f"正在解释{'块级' if formula_type == 'block' else '行内'}公式: {formula[:20]}...")
            
            system_prompt = """
            你是一个专业的数学公式解释助手。你的任务是解释学术论文中的数学公式，使其易于理解。
            
            请遵循以下指导：
            1. 解释公式中的各个符号和变量的含义
            2. 说明公式的整体含义和应用场景
            3. 如果适用，提供一个简单的例子
            4. 避免使用过于技术性的语言，使解释对初学者友好
            5. 简洁明了，每个解释不超过150词
            
            以Markdown格式返回解释。
            """
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请解释以下数学公式: {formula}"}
            ]
            
            explanation, usage = send_openai_request(client, messages, model)
            
            return {
                "formula": formula,
                "type": formula_type,
                "explanation": explanation or "无法获取解释"
            }
    
    def explain_section(section_data):
        """生成章节解释"""
        index, section = section_data
        with semaphore:
            print(f"正在解释章节 {index+1}/{len(sections)}: {section['title']}")
            
            system_prompt = """
            你是一个专业的学术内容解释助手。你的任务是解释学术论文章节的内容，使其易于理解。
            
            请遵循以下指导：
            1. 用简单的语言概述章节的主要内容
            2. 解释章节的主要观点和结论
            3. 指出章节中最重要的概念或方法
            4. 避免使用过于技术性的语言，使解释对初学者友好
            5. 简洁明了，每个解释不超过200词
            
            以Markdown格式返回解释。
            """
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请解释以下学术论文章节:\n\n{section['title']}\n\n{section['content']}"}
            ]
            
            explanation, usage = send_openai_request(client, messages, model)
            
            return {
                "title": section['title'],
                "explanation": explanation or "无法获取解释"
            }
    
    # 创建线程池
    with ThreadPoolExecutor(max_concurrency) as executor:
        print("第一阶段: 从各章节提取专业术语和公式...")
        # 使用线程池并发处理所有部分
        section_data = list(enumerate(sections))
        results = list(executor.map(extract_terms_from_section, section_data))
    
    # 对所有提取的术语和公式进行去重
    print("第二阶段: 合并并去重所有术语和公式...")
    unique_terms_formulas = deduplicate_terms_and_formulas(results)
    
    # 保存提取结果
    with open(terms_file, 'w', encoding='utf-8') as f:
        json.dump({
            "original_sections": results,
            "unique_terms_formulas": unique_terms_formulas
        }, f, ensure_ascii=False, indent=2)
    
    print(f"术语和公式已保存到: {terms_file}")
    
    # 并发生成术语解释
    print("第三阶段: 生成术语和公式解释...")
    
    with ThreadPoolExecutor(max_concurrency) as executor:
        # 准备术语和公式数据
        term_data = [(item['term'], item['category']) for item in unique_terms_formulas.get('terms', [])]
        formula_inline_data = [('inline', formula) for formula in unique_terms_formulas.get('formulas', {}).get('inline', [])]
        formula_block_data = [('block', formula) for formula in unique_terms_formulas.get('formulas', {}).get('block', [])]
        
        # 并发生成术语解释
        term_explanations = list(executor.map(generate_term_explanation, term_data)) if term_data else []
        
        # 并发生成公式解释
        formula_explanations = list(executor.map(generate_formula_explanation, formula_inline_data + formula_block_data)) if formula_inline_data + formula_block_data else []
    
    # 生成术语解释Markdown文件
    with open(explanations_file, 'w', encoding='utf-8') as f:
        f.write("# 术语和公式解释\n\n")
        
        # 添加术语解释
        if term_explanations:
            f.write("## 专业术语\n\n")
            for item in term_explanations:
                f.write(f"### {item['term']} ({item['category']})\n\n")
                f.write(f"{item['explanation']}\n\n")
        
        # 添加公式解释
        if formula_explanations:
            f.write("## 数学公式\n\n")
            for item in formula_explanations:
                formula_type = "块级公式" if item['type'] == 'block' else "行内公式"
                f.write(f"### {formula_type}\n\n")
                f.write(f"$${item['formula']}$$\n\n")
                f.write(f"{item['explanation']}\n\n")
    
    print(f"术语和公式解释已保存到: {explanations_file}")
    
    # 并发生成章节解释
    print("第四阶段: 生成章节解释...")
    
    with ThreadPoolExecutor(max_concurrency) as executor:
        # 准备章节数据
        section_data = list(enumerate(sections))
        
        # 并发生成章节解释
        section_explanations = list(executor.map(explain_section, section_data))
    
    # 生成章节解释Markdown文件
    with open(section_explanations_file, 'w', encoding='utf-8') as f:
        f.write("# 章节内容解释\n\n")
        
        for item in section_explanations:
            f.write(f"## {item['title']}\n\n")
            f.write(f"{item['explanation']}\n\n")
    
    print(f"章节解释已保存到: {section_explanations_file}")
    
    # 打印统计信息
    print("\n=== 处理完成 ===")
    print(f"总章节数: {len(sections)}")
    print(f"提取的唯一术语数: {len(unique_terms_formulas.get('terms', []))}")
    print(f"提取的唯一行内公式数: {len(unique_terms_formulas.get('formulas', {}).get('inline', []))}")
    print(f"提取的唯一块级公式数: {len(unique_terms_formulas.get('formulas', {}).get('block', []))}")
    print(f"API请求总数: {get_success}")
    
    if has_token_data:
        print(f"总Token消耗: {total_tokens}")
        print(f"  - 提示Token: {prompt_tokens}")
        print(f"  - 补全Token: {completion_tokens}")
    
    return {
        "sections": len(sections),
        "unique_terms": len(unique_terms_formulas.get('terms', [])),
        "unique_formulas_inline": len(unique_terms_formulas.get('formulas', {}).get('inline', [])),
        "unique_formulas_block": len(unique_terms_formulas.get('formulas', {}).get('block', [])),
        "api_requests": get_success,
        "total_tokens": total_tokens if has_token_data else None
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='学术论文专业术语提取与解释工具')
    parser.add_argument('--input_file', type=str,default="Paper/NODE/NODE_EN_zh.md", help='输入Markdown文件路径')
    parser.add_argument('--output_dir', type=str, default="Paper/NODE", help='输出目录路径')
    parser.add_argument('--model', type=str, default="bytedance/DeepSeek-V3", help='使用的模型')
    parser.add_argument('--max_rps', type=int, nargs=2, default=[12000,60], help='最大请求速率 [calls, period]')
    parser.add_argument('--max_concurrency', type=int, default=20, help='最大并发数')
    
    args = parser.parse_args()    
    extract_terms_and_formulas(
        input_file=args.input_file,
        output_dir=args.output_dir,
        model=args.model,
        max_rps=args.max_rps,
        max_concurrency=args.max_concurrency
    )
