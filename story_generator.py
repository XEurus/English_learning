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
from ratelimit import sleep_and_retry,limits

def read_words_from_file(file_path):
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
        print(f"保存提示词响应时出错: {e}")
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

def send_openai_request(client, messages, model, max_retries=2,temperature=0.7,max_tokens=4096):
    """发送OpenAI请求并处理响应"""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"API请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
            time.sleep(2)
    return None

def generate_story_with_openai(client, word_groups, prompts, model="MS/deepseek-r1", check_missing_words=True, regenerate_on_missing=False, system_prompt=None,conversation_file="story_generation_conversation.json"):
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
    
    for i, prompt_template in enumerate(prompts):
        # 获取当前组的单词（如果单词组不够，则循环使用）
        word_group = word_groups[i % len(word_groups)]
        
        # 将单词拼接到提示词前
        words_text = ", ".join(word_group)
        
        # 初始化尝试计数器
        max_attempts = 2  # 最大尝试次数
        attempt = 0
        success = False
        
        while attempt < max_attempts and not success:
            if attempt > 0:
                print(f"第 {attempt+1} 次尝试重新生成...")
            
            # 构建提示词
            prompt = f"小说所有主线剧情如下：{prompts}，当前需要撰写内容为第 {i+1}/{len(prompts)} 节，大致情节如下：{prompt_template}，根据之前的内容和当前情节纲要，续写小说新的部分，\
            使用以下单词: {words_text}\n\n"
            
            print(f"\n正在生成第 {i+1} 部分故事..." + (f" (尝试 {attempt+1}/{max_attempts})" if attempt > 0 else ""))
            print(f"提示词: {prompt_template}")
            print(f"使用的单词组: {words_text[:100]}...")  # 只显示前100个字符
            
            # 添加用户消息到历史记录
            current_messages = messages.copy()  # 创建消息历史的副本，避免失败的尝试污染历史
            current_messages.append({"role": "user", "content": prompt})
            
            story_part = send_openai_request(client, current_messages, model)
            if not story_part:
                attempt += 1
                continue
                
            # 过滤并处理内容
            story_part = filter_ai_content(story_part)
            
            # 检查是否包含所有单词
            if check_missing_words:
                missing_words = check_words_in_story(story_part, word_group)
                if missing_words and regenerate_on_missing:
                    print(f"警告：第 {i+1} 部分故事缺少以下单词: {', '.join(missing_words)}")
                    attempt += 1
                    continue  # 尝试重新生成
                elif missing_words:
                    print(f"警告：第 {i+1} 部分故事缺少以下单词: {', '.join(missing_words)}")
                    print("继续使用当前生成的内容...")
                    success = True  # 即使缺少单词也继续，因为regenerate_on_missing为False
                else:
                    print(f"第 {i+1} 部分故事已包含所有单词！")
                    success = True
            else:
                success = True  # 如果不检查单词，则认为成功
            
            # 如果生成成功，更新消息历史
            if success:
                messages.append({"role": "user", "content": prompt})
                messages.append({"role": "assistant", "content": story_part})
                
                # 保存当前对话历史到文件
                save_ai_response({
                    "part": i+1,
                    "prompt": prompt,
                    "response": story_part,
                    "full_messages": messages.copy()
                }, conversation_file, mode='a')
                
                responses.append(story_part)
                print(f"第 {i+1} 部分故事生成完成！")
        
        # 如果所有尝试都失败了
        if not success:
            print(f"警告：第 {i+1} 部分故事生成失败，已达到最大尝试次数 {max_attempts}")
            # 使用最后一次生成的内容，即使它不包含所有单词
            if story_part:
                messages.append({"role": "user", "content": prompt})
                messages.append({"role": "assistant", "content": story_part})
                responses.append(story_part)
                print(f"使用最后一次生成的内容继续...")
            else:
                # 如果完全失败，添加占位符
                responses.append(f"[第 {i+1} 部分生成失败]")
                
    # 将所有部分拼接起来
    full_story = "\n\n" + "\n\n".join(responses)
    return full_story

def generate_prompts_with_ai(client, num_prompts=10, model="gpt-3.5-turbo", system_prompt=None):
    """使用AI生成故事提示词"""
    try:
        j="""{"prompts": [{{"id": 1, "content": "大纲部分1"}}, {{"id": 2, "content": "大纲部分2"}}]}"""
        # 构建请求
        messages = [  
            {
                "role": "system",
                "content": f"""你是一位擅长写小说的作家，请为小说撰写不少于{num_prompts}个部分的小说大纲，要求可以连贯成整个故事，
            你的内容要用于扩展，需要严格遵守数量要求;用JSON格式返回大纲，
            返回格式为：{j},
            你回复的内容将被用于JSON解析，严格遵守返回格式，确保返回的数据结构正确，数量不少于{num_prompts}。
            """
            },
            {"role": "user", "content": f"{system_prompt}"},
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
        save_ai_response(response_data, "./generated_prompts.json", mode='a')
        
        def clean_json(json_content):
            decode_flag=False
            flag=1
            while not decode_flag:
                try:
                    data = json.loads(json_content)
                    return data

                except Exception as e:
                    if flag==1:
                        print(f"JSON解析错误: {e}")
                        # 尝试清理和修复JSON格式问题
                        cleaned_content = json_content.strip()
                        
                        # 处理多余的花括号
                        if cleaned_content.startswith('{{'):
                            cleaned_content = cleaned_content[1:]
                        if cleaned_content.endswith('}}'):
                            cleaned_content = cleaned_content[:-1]
                            
                        # 确保最外层是花括号
                        if not cleaned_content.startswith('{'):
                            cleaned_content = '{' + cleaned_content + '}'
                        data=cleaned_content
                        flag=2
                        continue
                    
                    if flag==2:
                        print(f"JSON解析错误: {e}")
                        print(f"原始响应内容: {response_content}")
                        fix_model="bytedance/DeepSeek-V3"
                        fixed_json = fix_json_with_llm(client,json_content,e,fix_model)
                        data = json.loads(fixed_json)
                        return data

        # 解析JSON响应
        try:
            # 尝试清理和修复JSON格式问题
            data=clean_json(response_content)
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

def generate_translation(client, story_file, output_dir, model="abab6.5s-chat",max_rps=[120, 60], max_concurrency=64):
    """
    逐行读取故事文件，使用AI翻译成中文，并生成纯中文文档和中英对照文档
    
    参数:
    client: OpenAI客户端
    story_file: 故事文件路径
    output_dir: 输出目录
    model: 使用的模型
    max_rps: 请求限制
    max_concurrency: 最大并发数
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
    semaphore = threading.Semaphore(max_concurrency)
    
    @sleep_and_retry
    @limits(calls=max_rps[0], period=max_rps[1])
    def send_translation_request(client, messages, model, max_retries=5, temperature=0.3, max_tokens=4096):
        return send_openai_request(client, messages, model, max_retries=max_retries, temperature=temperature, max_tokens=max_tokens)

    # 翻译单个段落的函数

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
                    ## 核心要求
                    1. 将英文输入翻译成中文
                    2. 在翻译内容的同时，括号内的内容按如下要求翻译：
                       将括号内中文注释 如 talisman（护身符）转换为 护身符（talisman）
                    """
                },
                {
                    "role": "user",
                    "content": f"{paragraph}"
                }
            ]
# 检测到英文输入时 → 输出中文翻译，检测到中文输入时 → 输出英文翻译
# - 中译英模式：将括号内注释如 矛盾心理（ambivalence）转换为 ambivalence（矛盾心理）格式            
            try:
                translation = send_translation_request(client, messages, model, max_retries=5, temperature=0.3, max_tokens=4096)
                with lock:
                    print(f"已翻译 {index+1}/{len(paragraphs)} 个段落")
                return index, translation
            except Exception as e:
                with lock:
                    print(f"翻译第 {index+1} 个段落时出错: {str(e)}")
                return index, f"[翻译错误: {str(e)}]"
    
    print(f"开始并发翻译，最大并发数: {max_concurrency}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
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

def fix_json_with_llm(client, json_string, error_message,model="bytedance/DeepSeek-V3"):
    """使用大语言模型修复JSON格式"""
    # 构建请求
    messages = [
        {
            "role": "system",
            "content": '''你是一个JSON格式修复器，你需要修复JSON内容到格式
            {"prompts": [{{"id": 1, "content": "大纲部分1"}}, {{"id": 2, "content": "大纲部分2"}}]}
            你只需要返回修复后的JSON内容，不要改变别的内容，不要返回多余内容。'''
        },
        {
            "role": "user",
            "content": f'''现在解析时碰到的问题是：{error_message}, 
            你只需要返回修复后的JSON内容，不要改变别的内容。所需要修复的内容为：{json_string},
            只允许返回修复后的JSON内容，不要改变别的内容，不要返回多余内容。'''
        }         
    ]
    
    # 发送请求并获取响应
    response_content = send_openai_request(client, messages, model,temperature=0.3,max_tokens=8192)
    
    # 返回修复后的JSON
    return response_content

def main(input_file='u1.txt',group_size=20,shuffle_choose=True):
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

    # 创建对话历史文件
    conversation_file = os.path.join(output_dir, "story_generation_conversation.json") 
    os.makedirs(os.path.dirname(conversation_file), exist_ok=True)
    # 复制输入文件到结果目录
    input_file_in_results = os.path.join(output_dir, os.path.basename(input_file))
    shutil.copy2(input_file, input_file_in_results)

    # 调用 word_counter.py 中的 shuffle_words_and_group 函数生成打乱的单词文件
    from word_counter import shuffle_words_and_group
    print(f"正在生成打乱的单词文件 '{shuffled_file}'...")
    shuffle_words_and_group(input_file_in_results, shuffled_file, group_size=group_size,shuffle=shuffle_choose)
    
    # 读取单词并分组
    word_groups = read_words_from_file(shuffled_file)
    if not word_groups:
        print("无法读取单词文件或分组单词。")
        sys.exit(1)    
    # 根据单词组数量决定故事段落数量
    num_prompts = len(word_groups)
    print(f"检测到{num_prompts}组单词，将生成{num_prompts}段故事")
    use_ai_prompts = input("\n是否使用AI生成提示词？(y/n): ").lower() == 'y'
    check_words = input("是否检查单词是否全部使用？(y/n): ").lower() == 'y'
    if check_words:
        regenerate_on_missing = input("如果有未使用的单词，是否重新生成？(y/n): ").lower() == 'y'
# - 刘慈欣风格科幻小说（重要）   
    # 设置提示词生成器的系统提示词
    ai_prompt_user_message = f"""小说剧情要求：
                                1.故事要求：
                                - 为狐妖小红娘撰写有趣的情感番外篇，要求符合官方设定
                                - 一个完整的故事，不要多个，段落之间要连贯
                                - 故事完整，细节丰富
                                2. 情节发展结构：
                                - 需呈现完整起承转合
                                - 每个阶段标注核心功能
                                - 严格遵守格式和数量要求。"""
    
    story_gen_system_message="""角色设定：
        你是一位专业的小说家和单词记忆专家，精通结合小说帮助学生记忆单词，现在你需要根据给出的**剧情大致内容**和**提供的单词**撰写小说，
        你通过故事发展帮助用户记忆单词。请将所有指定英文单词自然植入故事，需同时满足文学性与教学性目标。要求在符号意思的中文词语背后括号备注英文单词,方便用户结合上下文记忆单词。
        核心指令：
        1. 预生成检查：
        - 植入所有单词
        - 验证每个单词的植入合理性
        - 扫描不自然植入点（大于2个专业词汇连续出现时插入缓冲描写）
        2. 故事要求：
        - 为狐妖小红娘撰写有趣的情感番外篇，要求符合官方设定
        - 文字内容有条理，情节合理完整
        - 单词需自然融入对白、场景或心理描写
        - 根据提供的小说大致剧情，丰富、扩写该剧情，并和上下文串联，呈现完整起承转合
        - 每次生成必须使用所有指定单词，缺一不可（需严格检查）
        3. 输出格式：
        - 只输出中文故事，同时要求在提供的所需记忆的单词后括号备注英文，方便用户记忆。
        - 例如：护身符（talisman）在紫外线下泛着微光..."这不仅是首饰。"她低语，声音里的矛盾心理（ambivalence）在颤抖
        - 在最后列举所有单词和中文意思，用于用户回顾。
        """    
    # ai_prompt_user_message = """
    #             小说剧情要求：
    #             1.故事背景要求：
    #             - 中国背景
    #             - 具备知乎盐选风格（重要）
    #             - 玄幻小说
    #             2. 情节发展结构：
    #             - 需呈现完整起承转合
    #             - 每个阶段标注核心功能
    #             - 必须包含例如职场线+感情线这样的多线叙事
    #             3. 角色塑造维度：
    #             - 包含3人或以上的复杂人际关系网
    #             请按故事发展时序排列，保持现实主义基调同时制造戏剧张力，同时严格遵守格式和数量要求。"""
    
    # story_gen_system_message="""角色设定：
    #     你是一位专业的小说家和单词记忆专家，精通结合小说帮助学生记忆单词，现在你需要根据给出的**剧情大致内容**和**提供的单词**撰写小说，
    #     你通过故事发展帮助用户记忆单词。请将所有指定英文单词自然植入故事，需同时满足文学性与教学性目标。要求在符号意思的中文词语背后括号备注英文单词,方便用户结合上下文记忆单词。
    #     核心指令：
    #     1. 预生成检查：
    #     - 植入所有单词
    #     - 验证每个单词的植入合理性
    #     - 扫描不自然植入点（大于2个专业词汇连续出现时插入缓冲描写）
    #     2. 故事要求：
    #     - 背景：中国，知乎盐选风格，强剧情冲突
    #     - 玄幻小说
    #     - 文字内容有条理，情节合理完整
    #     - 单词需自然融入对白、场景或心理描写
    #     - 根据提供的小说大致剧情，丰富、扩写该剧情，并和上下文串联，呈现完整起承转合
    #     - 每次生成必须使用所有指定单词，缺一不可（需严格检查）
    #     3. 输出格式：
    #     - 只输出中文故事，同时要求在提供的所需记忆的单词后括号备注英文，方便用户记忆。
    #     - 例如：护身符（talisman）在紫外线下泛着微光..."这不仅是首饰。"她低语，声音里的矛盾心理（ambivalence）在颤抖"""

    # 直接设置API配置
    api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    model = "bytedance/DeepSeek-R1"  # 请替换为您想使用的模型名称
    translate_model = "bytedance/DeepSeek-V3"  # 请替换为您想使用的翻译模型名称
    # qwq-plus-128k
    # MS/deepseek-r1
    # NV/deepseek-r1
    # qwen-max
    # Doubao1.5pro-32k-character
    # abab6.5s-chat
    # bytedance/DeepSeek-V3
    # bytedance/DeepSeek-R1-search
    # bytedance/DeepSeek-R1
    # Ali/deepseek-r1-distill-llama-70b

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
        prompts = generate_prompts_with_ai(client, num_prompts, model, ai_prompt_user_message)
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
    time.sleep(60)  # 减少等待时间
    
    # 生成故事
    story = generate_story_with_openai(client, word_groups, prompts, model, check_words, regenerate_on_missing, story_gen_system_message,conversation_file)
    
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
    # abab6.5s-chat
    # Doubao1.5-32k
    # bytedance/DeepSeek-V3
    #generate_translation(client, output_file, output_dir,model=translate_model, max_rps=[120, 60], max_concurrency=32)

if __name__ == "__main__":
    main('u2.txt',5,shuffle_choose=False)
    # api_key = "sk-9FCirRxmIWGXD9N6CcFb46070bE243De990cCd976a3dF320"  # 请替换为您的API密钥
    # api_base = "http://192.168.5.122:33201/v1"  # 请替换为您的API基础URL
    # model = "abab6.5s-chat"  # 请替换为您想使用的模型名称
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
    # generate_translation(client,'./test.md','./',model=model, max_rps=[120, 60], max_concurrency=32)
