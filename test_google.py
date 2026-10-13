import requests


headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    # "available-dictionary": ":ESfPZlSW/6oz4Rb1bbM6UyYwPfxwv0dH+VPAaJg7KfU=:",
    "cache-control": "no-cache",
    "downlink": "0.35",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "rtt": "400",
    "sec-ch-prefers-color-scheme": "light",
    "sec-ch-ua": "\"Google Chrome\";v=\"155\", \"Chromium\";v=\"155\", \"Not(A:Brand\";v=\"24\"",
    "sec-ch-ua-arch": "\"x86\"",
    "sec-ch-ua-bitness": "\"64\"",
    "sec-ch-ua-form-factors": "\"Desktop\"",
    "sec-ch-ua-full-version": "\"155.0.8059.40\"",
    "sec-ch-ua-full-version-list": "\"Google Chrome\";v=\"155.0.8059.40\", \"Chromium\";v=\"155.0.8059.40\", \"Not(A:Brand\";v=\"24.0.0.0\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": "\"\"",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-ch-ua-platform-version": "\"10.0.0\"",
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2026 Google LLC. All Rights Reserved.",
    # "x-browser-validation": "0559nr5uGKWlIwYFRL6023LQGT0=",
    "x-browser-year": "2026",
    # "x-client-data": "CKmdygEIkqHLAQiFoM0BCNbolDAIoOyUMAj37JQwCPnslDAI/OyUMAj97JQwCIDtlDAI6++UMA=="
}
url = 'https://www.google.com/'
response = requests.get(url, proxies={"http": "127.0.0.1:3067"})

print("响应内容的实际url: ", response.url)
if response.url != url:
    print("请求时被重定向")
    if 'hk' in response.url:
        print('google 认为你在hk, 无法使用gemini或antigravity等服务')
    elif 'cn' in response.url:
        print('google 认为你在cn, 无法使用gemini或antigravity等服务')
    # other condition
    else:
        print("一切正常，可能能使用gemini或者antigravity，作为备选")   
        