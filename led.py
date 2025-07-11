import oneSActuatorLedPad as alp
from peripheral_one_s import *
import time
import math
from micropython import const

update_peripheral_info(force=True,no_wait=True)  # 刷新设备列表
found_devices = find_device_all([peripheral_list[0].device_id])  # 使用实际找到的 ID
if found_devices:
    peripheral = found_devices[0]  # 获取第一个设备

def rainbow_wave(cycles=3, speed=0.1):
    for _ in range(cycles):
        for i in range(len(alp.led_device_list)):
            for led_pos in range(alp.LED_NUM_MAX):
                # 计算彩虹色（确保输出在0-255范围内）
                hue = (i + led_pos) / max(alp.LED_NUM_MAX * 2, 1)  # 避免除以零
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                
                # 确保RGB值为整数且在0-255范围内
                r = max(0, min(255, int(r)))
                g = max(0, min(255, int(g)))
                b = max(0, min(255, int(b)))
                
                # 安全设置LED
                if 0 <= led_pos < alp.LED_NUM_MAX:
                    for controller in alp.led_device_list:
                        controller.set_rgb(led_pos, r, g, b)
            
            # 刷新显示
            for controller in alp.led_device_list:
                controller.refresh(peripheral)
            time.sleep(speed)

def hsv_to_rgb(h, s, v):
    h = max(0.0, min(1.0, h))  # 限制hue范围
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    
    # 返回整数元组
    if i == 0: return (int(v * 255), int(t * 255), int(p * 255))
    elif i == 1: return (int(q * 255), int(v * 255), int(p * 255))
    elif i == 2: return (int(p * 255), int(v * 255), int(t * 255))
    elif i == 3: return (int(p * 255), int(q * 255), int(v * 255))
    elif i == 4: return (int(t * 255), int(p * 255), int(v * 255))
    else: return (int(v * 255), int(p * 255), int(q * 255))


def ultra_smooth_breathing(color=(255, 0, 0), duration=1.2, steps=60):
    """
    修正版终极呼吸灯效果
    已修复const使用问题并优化：
    1. 正确的常量定义方式
    2. 增强的整数运算
    3. 更精确的亮度曲线
    """
    # 常量定义修正（使用正确的const语法）
    _GAMMA_NUM = const(220)  # 2.2 * 100
    _STEPS_TOTAL = steps * 2  # 不能直接const(steps*2)
    
    # 预计算亮度曲线（优化后的整数运算）
    brightness_lut = bytearray(_STEPS_TOTAL)
    scale = 10000  # 放大系数保持精度
    
    for i in range(_STEPS_TOTAL):
        t = (i * 100) // steps  # 0-200的整数
        
        if t < 100:
            # 缓入阶段：t^3.5 (0-100 → 0-100)
            t_scaled = t * 100  # 放大到0-10000
            val = (t_scaled * t_scaled * t_scaled * t) // (100*100*100)  # t^3.5近似
            val = int((val ** 0.28) / 100)  # 伽马校正
        else:
            # 缓出阶段：1-(t-100)^2.2 (100-0)
            t_scaled = (t-100) * 100
            val = 100 - (t_scaled * t_scaled * (t-100)) // 500000  # (t-100)^2.2近似
        
        brightness_lut[i] = min(255, val * 255 // 100)
    
    # 颜色分量预处理
    color_r, color_g, color_b = color
    last_rgb = None
    
    # 主循环优化
    t_start = time.ticks_ms()
    frame_count = 0
    
    while True:
        for i in range(_STEPS_TOTAL):
            brightness = brightness_lut[i]
            
            # 快速RGB计算（无分支优化）
            r = (color_r * brightness) >> 8
            g = (color_g * brightness) >> 8
            b = (color_b * brightness) >> 8
            current_rgb = (r, g, b)
            
            # 变化检测更新
            if current_rgb != last_rgb:
                for controller in alp.led_device_list:
                    # 最优更新策略选择
                    if hasattr(controller, 'fill'):
                        controller.fill(current_rgb)
                    elif hasattr(controller, 'set_all_leds'):
                        controller.set_all_leds(*current_rgb)
                    else:
                        for led_pos in range(alp.LED_NUM_MAX):
                            controller.set_rgb(led_pos, *current_rgb)
                    controller.refresh(peripheral)
                last_rgb = current_rgb
            
            # 自适应帧率控制
            frame_count += 1
            if frame_count % 10 == 0:
                elapsed = time.ticks_diff(time.ticks_ms(), t_start) / 1000
                target_time = duration * frame_count / _STEPS_TOTAL
                delay = max(0.001, (target_time - elapsed) / 10)
            else:
                delay = duration / (_STEPS_TOTAL * 1.5)
            
            time.sleep_us(int(delay * 1000000))  # 微秒级精度



# 运行彩虹波浪
rainbow_wave(cycles=5, speed=0.05)
