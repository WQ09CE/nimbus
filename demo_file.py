# 这是一个演示文件
def hello_world():
    print("你好，世界！")
    print("Hello, World!")
    return "success"

def add_numbers(a, b):
    return a + b

if __name__ == "__main__":
    hello_world()
    result = add_numbers(5, 3)
    print(f"5 + 3 = {result}")