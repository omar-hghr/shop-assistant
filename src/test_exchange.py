from tools import create_exchange_request

print(create_exchange_request("1001", "01012345678", "Black Hoodie L",
                              "too small", "Black Hoodie M", 5, True))
print(create_exchange_request("1002", "01123456789", "Blue Jeans 32",
                              "wrong size", "Blue Jeans 30", 20, True))
print(create_exchange_request("1003", "01234567890", "White T-Shirt M",
                              "changed my mind", "White T-Shirt L", 3, False))