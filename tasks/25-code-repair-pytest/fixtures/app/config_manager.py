def deep_update(base_dict, update_dict):
    """
    更新基础配置字典。
    """
    for key, value in update_dict.items():
        # 这里存在缺陷：如果 value 也是一个字典，它会直接覆盖，而不是深度合并
        base_dict[key] = value
    return base_dict