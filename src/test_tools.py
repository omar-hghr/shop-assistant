from tools import get_product_info, check_stock

print(get_product_info("hoodie"))
print(check_stock("hoodie", "L"))
print(check_stock("hoodie", "xl"))
print(check_stock("hoodie", "XXL"))
print(get_product_info("red jacket"))