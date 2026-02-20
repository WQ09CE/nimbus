def quicksort(arr):
    """
    快速排序算法实现
    
    Args:
        arr: 待排序的列表
    
    Returns:
        排序后的列表
    """
    # 基础情况：空数组或只有一个元素
    if len(arr) <= 1:
        return arr
    
    # 选择枢轴（pivot）- 这里选择中间元素
    pivot = arr[len(arr) // 2]
    
    # 分区：将数组分为三部分
    left = [x for x in arr if x < pivot]      # 小于枢轴的元素
    middle = [x for x in arr if x == pivot]   # 等于枢轴的元素
    right = [x for x in arr if x > pivot]     # 大于枢轴的元素
    
    # 递归排序并合并
    return quicksort(left) + middle + quicksort(right)


# 测试代码
if __name__ == "__main__":
    # 测试用例
    test_array = [3, 1, 4, 1, 5, 9, 2, 6]
    
    print("原始数组:", test_array)
    sorted_array = quicksort(test_array)
    print("排序后:", sorted_array)
    
    # 验证结果
    expected = [1, 1, 2, 3, 4, 5, 6, 9]
    if sorted_array == expected:
        print("✅ 测试通过！")
    else:
        print(f"❌ 测试失败！期望: {expected}, 实际: {sorted_array}")
