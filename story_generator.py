import os
import sys
import random
import time
import json
import datetime
import re
from openai import OpenAI
import shutil
import concurrent.futures
import threading
from ratelimit import sleep_and_retry

def read_words_from_file(file_path, group_size=20):
    """读取文件中的单词，并按指定大小分组"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        # 按空行分割内容，得到各个组
        groups = content.split('\n\n')
        # 过滤掉空组
        groups = [group.strip() for group in groups if group.strip()]    
        # 将每组单词转换为列表
        word_groups = []
        for group in groups:
            words = [word.strip() for word in group.split('\n') if word.strip()]
            word_groups.append(words)
        return word_groups
    except Exception as e:
        print(f"读取单词文件时出错: {e}")
        return []

def save_ai_response(response_data, file_path, mode='a'):
    """保存AI的响应到文件"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
         # 添加时间戳分隔符
        timestamp = f"\n\n--- AI响应时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n\n"
        # 将响应数据保存到文件
        with open(file_path, mode, encoding='utf-8') as f:
            if mode == 'a':
                f.write("\n\n" + "-" * 50 + "\n\n")
            
            # 如果是字典，则转换为JSON字符串
            if isinstance(response_data, dict):
                f.write(json.dumps(response_data, ensure_ascii=False, indent=2))
            else:
                f.write(str(response_data))
        
        return True
    except Exception as e:
        print(f"保存响应时出错: {e}")
        return False

def check_words_in_story(story, words):
    """检查所有单词是否都包含在故事中"""
    story_lower = story.lower()
    missing_words = []
    for word in words:
        word_lower = word.lower().strip()
        if word_lower and word_lower not in story_lower:
            missing_words.append(word)
    return missing_words

def filter_ai_content(content, type=''):
    """过滤AI生成内容中的思维链和无关内容"""
    # 如果内容为空，直接返回
    if not content:
        return ""
    
    # 移除<think>...</think>部分
    filtered = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    # 移除开头直到第一个标题或格式标记的内容
    filtered = re.sub(r'^.*?(?=###|\*\*\*|纯英文版本|Pure English Version)', '', filtered, flags=re.DOTALL)
    
    # 在promote模式下处理代码块
    if type == 'promote':
        # 处理JSON代码块
        if '```json' in filtered:
            filtered = filtered.split('```json')[1].split('```')[0].strip()
            filtered = '{' + filtered + '}'
        # 处理通用代码块
        elif '```' in filtered:
            filtered = filtered.split('```')[1].split('```')[0].strip()
        
    return filtered.strip()

def send_openai_request(client, messages, model, max_retries=2,temperature=0.7,max_tokens=8192):
    """发送OpenAI请求并处理响应"""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"API请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
            time.sleep(2)
    return None

def generate_story_with_openai(client, word_groups, prompts, model="gpt-3.5-turbo", check_missing_words=True, regenerate_on_missing=False, system_prompt=None):
    """使用OpenAI兼容API生成故事情节，保持对话历史"""
    if len(word_groups) < len(prompts):
        print(f"警告：单词组数量({len(word_groups)})少于提示词数量({len(prompts)})，将循环使用单词组")
    
    responses = []
    messages = [
        {"role": "system", "content": system_prompt or
        """角色设定：
            你是一位专业的小说家，现在你需要根据提示撰写小说张章节，将指定英文单词无缝融入小说，并通过故事发展帮助用户记忆词汇。每次生成需严格延续上文情节，确保连贯性。
            核心指令：
            故事要求：
            - 背景：现代/古风中国，知乎风格，强剧情冲突
            - 每次生成必须使用所有指定单词，缺一不可（需严格检查）
            - 单词需自然融入对白、场景或心理描写，禁止列表罗列
            - 我会给你小说剧情提示，请遵照提示发展小说。
            - 剧情应当生动、丰富，人物特点深刻鲜明。
            输出格式：
            分段对照，英文段落后紧跟对应中文翻译，单词后括号备注中文（例：He clutched the talisman（护身符） → 他紧握着护身符（talisman）），内容一致仅呈现方式不同。
            """},
        ]

    # 创建对话历史保存文件
    conversation_file = 'story_generation_conversation.json'
    
    for i, prompt_template in enumerate(prompts):
        # 获取当前组的单词（如果单词组不够，则循环使用）
        word_group = word_groups[i % len(word_groups)]
        
        # 将单词拼接到提示词前
        words_text = ", ".join(word_group)
        prompt = f"总的小说大纲主线为：{prompts}，一共有{len(prompts)}章，当前为第 {i+1} 章节，内容为：{prompt_template}，根据之前的内容，续写小说新的部分，\
        使用以下单词: {words_text}\n\n"
        
        print(f"\n正在生成第 {i+1} 部分故事...")
        print(f"提示词: {prompt_template}")
        print(f"使用的单词组: {words_text[:100]}...")  # 只显示前100个字符
        
        # 添加用户消息到历史记录
        messages.append({"role": "user", "content": prompt})
        
        story_part = send_openai_request(client, messages, model)
        if not story_part:
            continue
            
        # 过滤并处理内容
        story_part = filter_ai_content(story_part)
        messages.append({"role": "assistant", "content": story_part})
        
        
        max_attempts = 3  # 最大尝试次数
        attempt = 0
        # 检查是否包含所有单词
        if check_missing_words:
            missing_words = check_words_in_story(story_part, word_group)
            if missing_words:
                print(f"警告：第 {i+1} 部分故事缺少以下单词: {', '.join(missing_words)}")
                
                if regenerate_on_missing and attempt < max_attempts:
                    # 如果设置了重新生成标志，则继续循环
                    print("正在重新生成...")
                    attempt += 1
                    continue
            else:
                print(f"第 {i+1} 部分故事已包含所有单词！")
                
                # 将助手回复添加到历史记录中
                messages.append({"role": "assistant", "content": story_part})
                
                # 保存当前对话历史到文件
                save_ai_response({
                    "part": i+1,
                    "prompt": prompt,
                    "response": story_part,
                    "full_messages": messages.copy()
                }, conversation_file)
                
        responses.append(story_part)

        print(f"第 {i+1} 部分故事生成完成！")
        
    # 将所有部分拼接起来
    full_story = "\n\n" + "\n\n".join(responses)
    return full_story

def generate_prompts_with_ai(client, num_prompts=10, model="gpt-3.5-turbo", system_prompt=None):
    """使用AI生成故事提示词"""
    try:
        # 构建请求
        messages = [  
            {
                "role": "system",
                "content": system_prompt or 
                f"""你是一位擅长言情小说作家，请为小说撰写{num_prompts}个故事发展提示词，提示词可以连贯成整个故事，
                你的内容要用于扩展，需要严格遵守数量要求;用JSON格式返回提示词，
                返回格式为：{{"prompts": [{{"id": 1, "content": "提示词内容1"}}, {{"id": 2, "content": "提示词内容2"}}]}},
                你回复的内容将被用于JSON解析，严格遵守返回格式，确保返回的数据结构正确

                小说剧情要求：
                1.故事背景要求：
                - 中国背景
                - 具备知乎盐选风格
                2. 情节发展结构：
                - 需呈现完整起承转合
                - 每个阶段标注核心功能
                - 必须包含例如职场线+感情线这样的多线叙事
                3. 注意事项：
                - 结尾需保持开放性

                请按故事发展时序排列，保持现实主义基调同时制造戏剧张力，同时严格遵守格式和数量要求。"""
            }
        ]

        # 发送请求并获取响应
        print(messages)
        response_content = send_openai_request(client, messages, model)
        
        # 使用统一的过滤函数处理内容
        response_content = filter_ai_content(response_content, type='promote')
        
        # 保存请求和响应
        response_data = {
            "request": messages,
            "response": response_content,
            "model": model,
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        save_ai_response(response_data, "generated_prompts.json")
        
        # 解析JSON响应
        try:
            # 尝试清理和修复JSON格式问题
            cleaned_content = response_content.strip()
            
            # 检查并修复常见的JSON格式问题
            if cleaned_content.startswith('{{'):
                cleaned_content = cleaned_content[1:]
            if cleaned_content.endswith('}}'):
                cleaned_content = cleaned_content[:-1]
                
            # 确保我们处理的是JSON字符串
            if not cleaned_content.startswith('{'):
                cleaned_content = '{' + cleaned_content + '}'
            
            # 尝试处理可能的重复"prompts"键问题
            try:
                data = json.loads(cleaned_content)
            except json.JSONDecodeError as e:
                print(f"尝试修复JSON格式: {e}")
                # 使用正则表达式提取prompts数组
                import re
                prompts_match = re.search(r'"prompts"\s*:\s*\[(.*?)\]', cleaned_content, re.DOTALL)
                if prompts_match:
                    prompts_content = prompts_match.group(1)
                    # 构建新的JSON
                    fixed_json = '{{"prompts": [' + prompts_content + ']}}'
                    data = json.loads(fixed_json)
                else:
                    raise e
            
            prompts = data.get("prompts", [])
            
            # 验证提示词格式
            valid_prompts = []
            for prompt in prompts:
                if isinstance(prompt, dict) and "id" in prompt and "content" in prompt:
                    valid_prompts.append(prompt["content"])
            
            if not valid_prompts:
                print("未找到有效的提示词")
                return []
                
            return valid_prompts
        except Exception as e:
            print(f"JSON解析错误: {e}")
            print(f"原始响应内容: {response_content}")
            return []
        
    except Exception as e:
        print(f"生成提示词时出错: {e}")
        return []

def save_story_to_file(story, output_file):
    """将生成的故事保存到文件"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(story)
        print(f"\n故事已保存到 {output_file}")
        return True
    except Exception as e:
        print(f"保存故事时出错: {e}")
        return False

def get_default_prompts(num_prompts):
    """获取默认提示词"""
    default_prompts = [
        f"这个小说共有{num_prompts}个情节。请开始写第1个情节。",
        *[f"请继续写第{i+1}个情节，发展故事的主要冲突和事件。" for i in range(1, num_prompts-1)],
        f"请写第{num_prompts}个情节，为故事结尾。"
    ]
    return default_prompts

def generate_translation(client, story_file, output_dir, model="Tencent/DeepSeek-V3", max_workers=5):
    """
    逐行读取故事文件，使用AI翻译成中文，并生成纯中文文档和中英对照文档
    
    参数:
    client: OpenAI客户端
    story_file: 故事文件路径
    output_dir: 输出目录
    model: 使用的模型
    max_workers: 最大并发数
    """
    print("\n正在翻译故事内容...")
    
    # 读取故事文件
    try:
        with open(story_file, 'r', encoding='utf-8') as file:
            content = file.read()
    except Exception as e:
        print(f"读取故事文件时出错: {e}")
        return False
    
    # 按段落分割内容
    paragraphs = re.split(r'\n\s*\n', content)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
    print(f"共检测到 {len(paragraphs)} 个段落")
    
    # 创建输出文件路径
    chinese_only_file = os.path.join(output_dir, "chinese_only_story.txt")
    bilingual_file = os.path.join(output_dir, "bilingual_story.txt")
    
    # 用于存储翻译结果的字典，键为段落索引，值为翻译结果
    translations = {}
    
    # 用于线程安全的锁
    lock = threading.Lock()
    
    # 用于控制并发请求的信号量
    semaphore = threading.Semaphore(max_workers)
    
    # 翻译单个段落的函数
    @sleep_and_retry
    @limits(calls=20, period=10)
    def translate_paragraph(index, paragraph):
        if not paragraph.strip():
            return index, ""
        
        # 检查是否为不需要翻译的内容
        if re.match(r'^\s*(\|.*\|)+\s*$', paragraph) or re.match(r'^\s*!\[.*\]\(.*\)\s*$', paragraph):
            return index, paragraph
        
        with semaphore:
            messages = [
                {
                    "role": "system",
                    "content": 
                    """
                    # 智能对照翻译引擎协议v2.1
                    # You are a professional, authentic translation engine, only returns translations.
                    ## 核心要求
                    1. 输入语言智能识别：
                    - 检测到英文输入时 → 输出中文翻译
                    - 检测到中文输入时 → 输出英文翻译

                    2. 术语标注规范：
                    █ 英译中模式：
                    - 将括号内中文注释（如talisman（护身符））转换为「护身符（talisman）」格式
                    - 删除原英文单词的拼音标注（如有）
                    
                    █ 中译英模式：
                    - 将括号内注释（如矛盾心理（ambivalence））转换为「ambivalence（矛盾心理）」格式
                    - 保留中文原词的拼音标注（如有）

                    ## 质量要求
                    ✓ 文学性文本保留修辞手法
                    ✓ 科技文本确保术语准确性
                    ✓ 口语化表达符合目标语言习惯
                    """
                },
                {
                    "role": "user",
                    "content": f"{paragraph}"
                }
            ]
            
            try:
                translation = send_openai_request(client, messages, model, max_retries=2, temperature=0.3, max_tokens=8192)
                with lock:
                    print(f"已翻译 {index+1}/{len(paragraphs)} 个段落")
                return index, translation
            except Exception as e:
                with lock:
                    print(f"翻译第 {index+1} 个段落时出错: {str(e)}")
                return index, f"[翻译错误: {str(e)}]"
    
    print(f"开始并发翻译，最大并发数: {max_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有翻译任务
        future_to_index = {executor.submit(translate_paragraph, i, para): i for i, para in enumerate(paragraphs)}
        
        # 获取结果
        for future in concurrent.futures.as_completed(future_to_index):
            index, translation = future.result()
            translations[index] = translation
    
    # 按原始顺序组织翻译结果
    ordered_translations = [translations.get(i, "") for i in range(len(paragraphs))]
    
    # 生成纯中文文档
    try:
        with open(chinese_only_file, 'w', encoding='utf-8') as file:
            for translation in ordered_translations:
                if translation:
                    file.write(translation + "\n\n")
        print(f"纯中文文档已保存到 {chinese_only_file}")
    except Exception as e:
        print(f"保存纯中文文档时出错: {e}")
    
    # 生成中英对照文档
    try:
        with open(bilingual_file, 'w', encoding='utf-8') as file:
            for i, (paragraph, translation) in enumerate(zip(paragraphs, ordered_translations)):
                file.write(paragraph + "\n\n")
                file.write(translation + "\n\n")
                file.write("-" * 80 + "\n\n")
        print(f"中英对照文档已保存到 {bilingual_file}")
    except Exception as e:
        print(f"保存中英对照文档时出错: {e}")
    
    return True

def main(input_file='u1.txt',group_size=20):
    """主函数"""
    # 设置默认值
    if not os.path.exists(input_file):
        print(f"单词文件 '{input_file}' 未找到。")
        return
    # 创建基于时间戳的输出目录
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = f"results_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    # 设置输出文件路径
    output_file = os.path.join(output_dir, "generated_story.txt")
    prompts_file = os.path.join(output_dir, "generated_prompts.json")
    not_found_words_file = os.path.join(output_dir, "not_found_words.txt")
    shuffled_file = os.path.join(output_dir, "shuffled_words.txt")  # 打乱后的单词文件
    
    # 复制输入文件到结果目录
    input_file_in_results = os.path.join(output_dir, os.path.basename(input_file))
    shutil.copy2(input_file, input_file_in_results)

    # 调用 word_counter.py 中的 shuffle_words_and_group 函数生成打乱的单词文件
    from word_counter import shuffle_words_and_group
    print(f"正在生成打乱的单词文件 '{shuffled_file}'...")
    shuffle_words_and_group(input_file_in_results, shuffled_file, group_size=group_size)
    
    # 读取单词并分组
    word_groups = read_words_from_file(shuffled_file)
    if not word_groups:
        print("无法读取单词文件或分组单词。")
        sys.exit(1)
    
    # 根据单词组数量决定故事段落数量
    num_prompts = len(word_groups)
    print(f"检测到{num_prompts}组单词，将生成{num_prompts}段故事")
    
    # 询问用户是否使用AI生成提示词
    use_ai_prompts = input("\n是否使用AI生成提示词？(y/n): ").lower() == 'y'
    
    # 设置是否检查单词和重新生成
    check_words = input("是否检查单词是否全部使用？(y/n): ").lower() == 'y'
    
    if check_words:
        regenerate_on_missing = input("如果有未使用的单词，是否重新生成？(y/n): ").lower() == 'y'
    
    # 设置提示词生成器的系统提示词
    ai_prompt_system_message = f"""你是一位擅长写言情小说的作家，请为小说撰写不少于{num_prompts}个部分的小说大纲，要求可以连贯成整个故事，
            你的内容要用于扩展，需要严格遵守数量要求;用JSON格式返回大纲，
            返回格式为：{{"prompts": [{{"id": 1, "content": "大纲部分1"}}, {{"id": 2, "content": "大纲部分2"}}]}},
            你回复的内容将被用于JSON解析，严格遵守返回格式，确保返回的数据结构正确，数量不少于{num_prompts}。

            小说剧情要求：
            1.故事背景要求：
            - 中国背景
            - 具备知乎盐选风格（重要）
            2. 情节发展结构：
            - 需呈现完整起承转合
            - 每个阶段标注核心功能
            - 必须包含例如职场线+感情线这样的多线叙事
            3. 角色塑造维度：
            - 双强设定但有致命弱点
            - 包含3人或以上的复杂人际关系网
            4. 注意事项：
            - 结尾需保持开放性
            请按故事发展时序排列，保持现实主义基调同时制造戏剧张力，同时要严格遵守格式和数量要求。"""
    
    story_gen_system_message="""角色设定：
        你是一位专业的小说家，精通英文小说创作，现在你需要根据**剧情大纲**和**主线提示**撰写小说张章节，请将指定英文词汇自然植入故事，需同时满足文学性与教学性目标。
        并通过故事发展帮助用户记忆词汇。每次生成需严格延续上文情节，确保连贯性。
        核心指令：
        1.词汇融合机制
        - 重要词汇在悬念揭晓时复现
        - 同根词在相邻章节梯度出现（例：solve → resolve）

        2. 预生成检查：
        - 验证每个单词的植入合理性
        - 扫描不自然植入点（大于2个专业词汇连续出现时插入缓冲描写）

        3. 故事要求：
        - 背景：中国，知乎盐选风格，强剧情冲突
        - 每次生成必须使用所有指定单词，缺一不可（需严格检查）
        - 单词需自然融入对白、场景或心理描写，禁止列表罗列
        - 我会给你小说剧情大纲主线和提示，请按照主线发展小说。
        - 在嵌入单词的同时剧情应当有趣生动，丰富流畅，上下文衔接不能出现过大的裂隙

        4. 输出格式：
        The talisman（护身符） emitted faint fluorescence under UV light... "This isn't mere jewelry." She whispered, ambivalence（矛盾心理） trembling in her voice.
        只需要输出英文故事，在所需单词后括号备注中文 
        """    

    # 直接设置API配置
    api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    model = "MS/deepseek-r1"  # 请替换为您想使用的模型名称
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
    # 生成提示词
    if use_ai_prompts:
        print("\n正在使用AI生成提示词...")
        prompts = generate_prompts_with_ai(client, num_prompts, model, ai_prompt_system_message)
        if prompts:
            # 保存生成的提示词到结果目录
            prompts_data = {"prompts": [{"id": i+1, "content": prompt} for i, prompt in enumerate(prompts)]}
            save_ai_response(prompts_data, prompts_file, mode='w')
            print(f"提示词已保存到 {prompts_file}")
        else:
            print("AI生成提示词失败，将使用默认提示词")
            use_ai_prompts = False
    
    if not use_ai_prompts:
        # 使用默认提示词
        print("\n使用默认提示词...")
        prompts = get_default_prompts(num_prompts)
        # 保存默认提示词到结果目录
        prompts_data = {"prompts": [{"id": i+1, "content": prompt} for i, prompt in enumerate(prompts)]}
        save_ai_response(prompts_data, prompts_file, mode='w')
        print(f"默认提示词已保存到 {prompts_file}")
    
    # 显示生成的提示词
    print("\n将使用以下提示词生成故事：")
    for i, prompt in enumerate(prompts):
        print(f"{i+1}. {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
    time.sleep(3)  # 减少等待时间
    
    # 生成故事
    story = generate_story_with_openai(client, word_groups, prompts, model, check_words, regenerate_on_missing, story_gen_system_message)
    
    # 保存故事
    save_story_to_file(story, output_file)
    
    # 如果有未找到的单词，保存到文件
    if check_words:
        missing_words = check_words_in_story(story, [word for group in word_groups for word in group])
        if missing_words:
            with open(not_found_words_file, 'w', encoding='utf-8') as f:
                f.write("\n".join(missing_words))
            print(f"未找到的单词已保存到 {not_found_words_file}")
    
    print("\n处理完成！")
    print(f"所有结果已保存到目录: {output_dir}")
    
    # 运行单词计数器
    print(f"正在检查单词在生成的故事中的出现情况...")
    from word_counter import counter 
    counter(input_file_in_results, output_file, output_dir)
    
    # 运行翻译器
    print(f"正在翻译生成的故事...")
    generate_translation(client, output_file, output_dir)

def translate_paper():
    api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    model = "abab6.5s-chat"  # 请替换为您想使用的模型名称
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

    if not os.path.exists(input_file):
        print(f"单词文件 '{input_file}' 未找到。")
        return
    # 创建基于时间戳的输出目录
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = f"results_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    # 设置输出文件路径
    output_file = os.path.join(output_dir, "generated_story.txt")
    prompts_file = os.path.join(output_dir, "generated_prompts.json")
    not_found_words_file = os.path.join(output_dir, "not_found_words.txt")
    shuffled_file = os.path.join(output_dir, "shuffled_words.txt")  # 打乱后的单词文件        
    generate_translation(client,'./results_20250306_174228/generated_story.txt', './results_20250306_174228',model=model)


if __name__ == "__main__":
    main('u1.txt', 5)
    # api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    # api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    # model = "MS/deepseek-r1"  # 请替换为您想使用的模型名称
    # # 初始化OpenAI客户端
    # client_kwargs = {"api_key": api_key}
    # if api_base:
    #     client_kwargs["base_url"] = api_base
    
    # try:
    #     client = OpenAI(**client_kwargs)
    #     print(f"已连接到API服务: {api_base}")
    # except Exception as e:
    #     print(f"初始化API客户端时出错: {e}")
    #     sys.exit(1)        
    # generate_translation(client,'./results_20250306_174228/generated_story.txt', './results_20250306_174228')
