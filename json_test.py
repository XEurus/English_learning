import json
def extract_headwords_from_file(file_path):
    """
    从文件中逐行提取JSON数据，并从每个JSON块中提取headWord字段。

    参数:
    file_path (str): 文件路径，文件包含多个JSON块。

    返回:
    list: 包含所有提取的headWord的列表。
    """
    headwords = []

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除可能的空行和首尾空白
                line = line.strip()
                if line:  # 只有非空行才处理
                    try:
                        data = json.loads(line)  # 将每一行解析为JSON对象
                        head_word = data['content']['word']['wordHead']
                        headwords.append(head_word)
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"Error processing line: {line}\nError: {e}")
                        continue  # 跳过错误的行

    except FileNotFoundError:
        print(f"File not found: {file_path}")

    return headwords


# 示例使用
file_path = './CET6_2.json'  # 假设这是包含JSON数据的文件
headwords = extract_headwords_from_file(file_path)

# 打印所有提取到的headWord
for i, word in enumerate(headwords, 1):
    print(f"HeadWord {i}: {word}")