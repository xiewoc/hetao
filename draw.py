from machine import Pin, SPI, PWM
import ST7735
import time
import urequests
import ubinascii
import ujson as json
import uio
import os
import uos
import ustruct
import uzlib
from io import BytesIO

# 引脚映射
gpio_map = {
    "tft_sck": 37,
    "tft_mosi": 38,
    "tft_dc": 36,
    "tft_reset": 35,
    "tft_cs": 34,
    "tft_bgl": 33
}

def text2image(url, model_name, step, sampler, height, width, CLIP, seed, prompt, negative_prompt):
    payload = {
        "override_settings": {
            "sd_model_checkpoint": model_name,
            "sd_vae": "",
            "CLIP_stop_at_last_layers": CLIP,
        },
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "steps": step,
        "sampler_name": sampler,
        "width": width,
        "height": height,
        "batch_size": 1,
        "n_iter": 1,
        "seed": seed,
    }

    try:
        # 发送请求
        response = urequests.post(url=f'{url}/sdapi/v1/txt2img', json=payload)
        r = response.json()
        
        if "images" not in r:
            print("Error: No images in response")
            return None

        # 解码 Base64 并保存为 PNG
        image_data = ubinascii.a2b_base64(r['images'][0])
        with open("opt.png", 'wb') as f:
            f.write(image_data)
        
        return 'opt.png'  # 返回 PNG 文件路径

    except Exception as e:
        print("Error:", e)
        return None
    finally:
        response.close()

def load_24bit_bmp(filename):
    with open(filename, 'rb') as f:
        # 检查BMP文件头
        if f.read(2) != b'BM':
            raise ValueError("Not a valid BMP file")
        
        # 读取文件大小和像素数据偏移量
        file_size = int.from_bytes(f.read(4), 'little')
        f.read(4)  # 保留字段
        pixel_offset = int.from_bytes(f.read(4), 'little')
        
        # 读取DIB头
        dib_size = int.from_bytes(f.read(4), 'little')
        width = int.from_bytes(f.read(4), 'little')
        height = int.from_bytes(f.read(4), 'little')
        f.read(2)  # 颜色平面
        bpp = int.from_bytes(f.read(2), 'little')
        compression = int.from_bytes(f.read(4), 'little')
        
        if bpp != 24:
            raise ValueError("Only 24-bit BMP supported")
        if compression != 0:
            raise ValueError("Compressed BMP not supported")
        
        # 计算行字节数（每行需要4字节对齐）
        row_size = (width * 3 + 3) & ~3
        
        # 读取像素数据
        f.seek(pixel_offset)
        rgb565_data = bytearray()
        
        for y in range(height-1, -1, -1):  # BMP是倒序存储
            f.seek(pixel_offset + y * row_size)
            for x in range(width):
                # 读取BGR格式数据（BMP存储顺序）
                b = ord(f.read(1))
                g = ord(f.read(1))
                r = ord(f.read(1))
                
                # 转换为RGB565
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                rgb565_data.extend(rgb565.to_bytes(2, 'big'))
    
    return width, height, rgb565_data

import struct
import zlib

def paeth_predictor(a, b, c):
    """Paeth predictor function."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    else:
        return c

def unfilter_scanline(filter_type, scanline, prev_scanline, bpp):
    """Unfilter a single scanline based on the filter type."""
    unfiltered = bytearray(len(scanline))
    
    if filter_type == 0:  # None
        unfiltered[:] = scanline
    elif filter_type == 1:  # Sub
        for i in range(len(scanline)):
            x_bpp = max(0, i - bpp)
            unfiltered[i] = (scanline[i] + unfiltered[x_bpp]) & 0xFF
    elif filter_type == 2:  # Up
        for i in range(len(scanline)):
            unfiltered[i] = (scanline[i] + prev_scanline[i]) & 0xFF
    elif filter_type == 3:  # Average
        for i in range(len(scanline)):
            x_bpp = max(0, i - bpp)
            average = ((unfiltered[x_bpp] if i >= bpp else 0) + prev_scanline[i]) // 2
            unfiltered[i] = (scanline[i] + average) & 0xFF
    elif filter_type == 4:  # Paeth
        for i in range(len(scanline)):
            x_bpp = max(0, i - bpp)
            pred = paeth_predictor(unfiltered[x_bpp] if i >= bpp else 0, prev_scanline[i], prev_scanline[x_bpp] if i >= bpp else 0)
            unfiltered[i] = (scanline[i] + pred) & 0xFF
    
    return unfiltered

def load_png_to_rgb565(filename):
    with open(filename, 'rb') as f:
        # 检查 PNG 文件签名
        if f.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError("Not a valid PNG file")
        
        def read_chunk():
            chunk_length = int.from_bytes(f.read(4), 'big')
            chunk_type = f.read(4)
            chunk_data = f.read(chunk_length)
            chunk_crc = f.read(4)  # CRC 校验值（暂不验证）
            return chunk_type, chunk_data
        
        width = height = bit_depth = color_type = None
        pixel_data = bytearray()
        
        while True:
            chunk_type, chunk_data = read_chunk()
            
            if chunk_type == b'IHDR':  # 图像头部信息
                width, height, bit_depth, color_type = struct.unpack('>IIBB', chunk_data[:10])
                
                if (color_type, bit_depth) not in [(2, 8), (6, 8), (0, 16)]:
                    raise ValueError("Unsupported PNG format (only 24-bit RGB, 32-bit RGBA, and 16-bit Grayscale are supported)")
            
            elif chunk_type == b'IDAT':  # 图像数据块
                pixel_data.extend(chunk_data)
            
            elif chunk_type == b'IEND':  # 文件结束块
                break
        
        decompressed_data = zlib.decompress(pixel_data)
        rgb565_data = bytearray()
        prev_scanline = bytearray(width * (3 if color_type == 2 else 4))  # 初始化上一行数据
        bpp = 3 if color_type == 2 else 4  # Bytes per pixel for RGB or RGBA images
        
        row_size = width * bpp + 1  # 每行包括滤波器字节
        
        for y in range(height):
            filter_type = decompressed_data[0]
            scanline = decompressed_data[1:row_size]
            decompressed_data = decompressed_data[row_size:]
            
            unfiltered = unfilter_scanline(filter_type, scanline, prev_scanline, bpp)
            prev_scanline = unfiltered
            
            if color_type == 2 and bit_depth == 8:  # 24-bit RGB
                for x in range(width):
                    idx = x * 3
                    r, g, b = unfiltered[idx:idx+3]
                    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    rgb565_data.extend(struct.pack('>H', rgb565))
            elif color_type == 6 and bit_depth == 8:  # 32-bit RGBA
                for x in range(width):
                    idx = x * 4
                    r, g, b, _ = unfiltered[idx:idx+4]  # 忽略 Alpha 通道
                    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    rgb565_data.extend(struct.pack('>H', rgb565))
            elif color_type == 0 and bit_depth == 16:  # 16-bit Grayscale
                for x in range(width):
                    idx = x * 2
                    gray_high, gray_low = unfiltered[idx:idx+2]
                    gray = (gray_high << 8) | gray_low  # 组合 16 位灰度值
                    
                    # 将灰度值转换为 RGB565 格式
                    r = g = b = (gray >> 8)  # 使用高 8 位灰度值
                    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    rgb565_data.extend(struct.pack('>H', rgb565))
    
    return width, height, rgb565_data
def rotate_90_clockwise(width, height, data):
    """
    将图像数据顺时针旋转 90°。
    :param width: 原始图像宽度
    :param height: 原始图像高度
    :param data: RGB565 格式的图像数据
    :return: 旋转后的新宽度、新高度和新图像数据
    """
    # 创建一个新的 bytearray，用于存储旋转后的图像数据
    new_width = height
    new_height = width
    new_data = bytearray(new_width * new_height * 2)  # 每个像素 2 字节（RGB565）

    # 遍历原始图像的每个像素，并将其放置到旋转后的位置
    for y in range(height):
        for x in range(width):
            # 原始像素位置
            src_idx = (y * width + x) * 2
            
            # 旋转后的位置：(x, y) -> (height - 1 - y, x)
            dst_x = height - 1 - y
            dst_y = x
            dst_idx = (dst_y * new_width + dst_x) * 2
            
            # 将像素数据复制到新位置
            new_data[dst_idx:dst_idx+2] = data[src_idx:src_idx+2]
    
    return new_width, new_height, new_data

def show_bmp(filename, x=0, y=0):
    # 加载并转换图像
    w, h, data = load_24bit_bmp(filename)
    
    # 分块显示（避免内存不足）
    chunk_h = 20  # 每次处理20行
    for y_pos in range(0, h, chunk_h):
        current_h = min(chunk_h, h - y_pos)
        start = y_pos * w * 2
        end = start + current_h * w * 2
        
        tft.image(x, y + y_pos,
                 x + w - 1,
                 y + y_pos + current_h - 1,
                 data[start:end])
        
        print(f"显示进度: {min(y_pos+chunk_h, h)}/{h}")
        

def show_png(filename, x=0, y=0):
    # 加载并转换图像
    w, h, data = load_png_to_rgb565(filename)
    
    # 旋转图像 90°
    rotated_w, rotated_h, rotated_data = rotate_90_clockwise(w, h, data)
    
    # 显示旋转后的图像
    tft.image(x, y, x + rotated_w - 1, y + rotated_h - 1, rotated_data)
    
    print(f"旋转后的图像已显示: 宽度={rotated_w}, 高度={rotated_h}")

# WiFi 配置
WIFI_SSID = "19-1"
WIFI_PASSWORD = "XJNmama800212"

# 连接到 WiFi
def connect_wifi():
    import network
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        while not wlan.isconnected():
            pass

# 主程序
connect_wifi()
url = "http://192.168.0.44:7860" 
#text = input("提示词：")
#bmp_path = text2image(url,"", 35, "Euler a", 256, 256, 1, -1, text, "(worst quality:2), (low quality:2), (normal quality:2), lowres, ((monochrome)), ((grayscale)), bad anatomy,DeepNegative, skin spots, acnes, skin blemishes,(fat:1.2),facing away, looking away,tilted head, lowres,bad anatomy,bad hands, missing fingers,extra digit, fewer digits,bad feet,poorly drawn hands,poorly drawn face,mutation,deformed,extra fingers,extra limbs,extra arms,extra legs,malformed limbs,fused fingers,too many fingers,long neck,cross-eyed,mutated hands,polar lowres,bad body,bad proportions,gross proportions,missing arms,missing legs,extra digit, extra arms, extra leg, extra foot,teethcroppe,signature, watermark, username,blurry,cropped,jpeg artifacts,text,error,")

from resize import resize_bmp_nearest
resize_bmp_nearest("logo2.bmp", "logo2-1.bmp", 64, 64)

# 1. 手动复位显示屏
reset_pin = Pin(gpio_map["tft_reset"], Pin.OUT)
reset_pin.value(0)  # 拉低复位
time.sleep_ms(100)
reset_pin.value(1)  # 释放复位
time.sleep_ms(100)

# 2. 配置SPI
spi = SPI(
    1,  # 使用SPI1 (HSPI)
    baudrate=40_000_000,
    polarity=0,
    phase=0,
    sck=Pin(gpio_map["tft_sck"]),
    mosi=Pin(gpio_map["tft_mosi"]),
    miso=None
)

# 3. 初始化TFT (关键修改：直接传递引脚号而非Pin对象)
try:
    tft = ST7735.TFT(
        spi,                # SPI接口
        gpio_map["tft_dc"],     # 直接传递DC引脚号
        gpio_map["tft_reset"],  # 直接传递RESET引脚号
        gpio_map["tft_cs"]      # 直接传递CS引脚号
    )
    
    # 尝试初始化
    print("尝试initr初始化...")
    tft.initr()  # 红色标签初始化
    tft.rgb(True)  # RGB颜色模式
    
except Exception as e:
    print(f"初始化失败: {e}")
    if 'tft' in locals():
        try:
            print("尝试initb初始化...")
            tft.initb()  # 蓝色标签初始化
            tft.rgb(False)
        except Exception as e2:
            print(f"备用初始化失败: {e2}")
            raise

# 4. 背光控制
backlight = PWM(Pin(gpio_map["tft_bgl"]))
backlight.freq(1000)
backlight.duty_u16(32768)  # 50%亮度

# 5. 测试显示
if 'tft' in locals():
    try:
        tft.fill(tft.WHITE)
        show_png("logo.png", 0, 0)
        print("显示测试成功!")
    except Exception as e:
        print(f"显示测试失败: {e}")
else:
    print("显示屏未初始化成功")
