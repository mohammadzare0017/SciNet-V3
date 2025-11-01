import random
import asyncio
from pathlib import Path



async def human_sleep(a=0.3, b=1.2):
    await asyncio.sleep(random.uniform(a, b))

async def human_type(page, selector, text, min_delay=50, max_delay=150):
    await page.click(selector)
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(random.uniform(min_delay, max_delay) / 1000.0)

async def human_move_mouse(page, times=5):
    for _ in range(times):
        x = random.randint(50, 1300)
        y = random.randint(50, 700)
        await page.mouse.move(x, y, steps=random.randint(8, 20))
        await asyncio.sleep(random.uniform(0.1, 0.6))
