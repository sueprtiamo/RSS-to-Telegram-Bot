from __future__ import annotations
from typing import Optional

import numpy as np
from math import ceil
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup
from matplotlib import pyplot as plt
from matplotlib.font_manager import FontManager
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from cjkwrap import fill
from warnings import filterwarnings

from src import env
from .utils import logger

_matplotlib_thread_pool = ThreadPoolExecutor(1, 'matplotlib_')

MPL_TTF_LIST = FontManager().ttflist
MPL_SANS_FONTS = \
    list(f.name for f in MPL_TTF_LIST if f.name == 'WenQuanYi Micro Hei') \
    + list(f.name for f in MPL_TTF_LIST if f.name == 'WenQuanYi Zen Hei') \
    + list({f.name for f in MPL_TTF_LIST if f.name.startswith('Noto Sans CJK')}) \
    + list({f.name for f in MPL_TTF_LIST if f.name.startswith('Microsoft YaHei')}) \
    + list({f.name for f in MPL_TTF_LIST if f.name in {'SimHei', 'SimKai', 'SimSun', 'SimSun-ExtB'}}) \
    + list({f.name for f in MPL_TTF_LIST if f.name.startswith('Noto Sans') and 'cjk' in f.name.lower()}) \
    + list({f.name for f in MPL_TTF_LIST if not f.name.startswith('Noto Sans') and 'sans' in f.name.lower()})

plt.rcParams['font.sans-serif'] = MPL_SANS_FONTS
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False

filterwarnings('error', 'constrained_layout not applied', UserWarning)


def _convert_table_to_png(table_html: str) -> Optional[BytesIO]:
    soup = BeautifulSoup(table_html, 'lxml')
    table = soup.find('table')
    if not table:
        return None
    wrap_length = 85
    column_labels: list[str] = []
    row_labels: list[str] = []
    cell_texts: list[list[str]] = []
    thead = table.find('thead')
    try:
        if thead:
            column_labels = [label.text for label in thead.find_all('th')]
            thead.decompose()
        else:
            maybe_thead = table.find('tr')
            if maybe_thead:
                ths = maybe_thead.find_all('th')
                if len(ths) > 1:
                    column_labels = [label.text for label in ths]
                    maybe_thead.decompose()
        rows = table.find_all('tr')
        if rows:
            for ori_width in rows:
                th = ori_width.find('th')
                if th:
                    row_labels.append(th.text)
                cell_texts.append([cell.text for cell in ori_width.find_all('td')])
        if not cell_texts:
            if column_labels:
                cell_texts.append(column_labels)
                column_labels = row_labels = []
            elif row_labels:
                cell_texts = [[label] for label in row_labels]
                column_labels = row_labels = []
            else:
                return None
        # ensure row number and column number
        max_columns = max(max(len(row) for row in cell_texts), len(column_labels))
        max_rows = max(len(cell_texts), len(row_labels))
        if min(max_columns, max_rows) == 0:
            return None
        if column_labels and len(column_labels) < max_columns:
            column_labels += [''] * (max_columns - len(column_labels))
        if row_labels and len(row_labels) < max_rows:
            row_labels += [''] * (max_rows - len(row_labels))
        if len(cell_texts) < max_rows:
            cell_texts += [[''] * max_columns] * (max_rows - len(cell_texts))
        wrap_length = max(wrap_length // max_columns, 10)

        auto_set_column_width_flag = True
        for tries in range(2):
            # draw table
            fig, ax = plt.subplots(figsize=(8, 8))
            table = ax.table(cellText=cell_texts,
                             rowLabels=row_labels or None,
                             colLabels=column_labels or None,
                             loc='center',
                             cellLoc='center',
                             rowLoc='center')
            row_heights = defaultdict(lambda: 0)
            if auto_set_column_width_flag:
                table.auto_set_column_width(tuple(range(max_columns)))
            # set row height
            for xy, cell in table.get_celld().items():
                text = cell.get_text().get_text()
                text = fill(text.strip(), wrap_length)
                cell.get_text().set_text(text)
                row_heights[xy[0]] = max(
                    cell.get_height() * (text.count('\n') + 1) * 0.75 + cell.get_height() * 0.25,
                    row_heights[xy[0]]
                )
            for xy, cell in table.get_celld().items():
                cell.set_height(row_heights[xy[0]])
            fig.set_constrained_layout(True)
            ax.axis('off')
            buffer = BytesIO()
            try:
                fig.savefig(buffer, format='png', dpi=200)
            except UserWarning:
                # if auto_set_column_width_flag:
                #     auto_set_column_width_flag = False  # oops, overflowed!
                #     continue  # once a figure is exported, some stuff may be frozen, so we need to re-create the table
                return None

            # crop
            image = Image.open(buffer)
            ori_width, ori_height = image.size
            upper = left = float('inf')
            lower = right = float('-inf')
            # noinspection PyTypeChecker
            ia = np.array(image)
            # trim white border
            for r in range(ori_height):
                if min(ia[r][c][0] for c in range(ori_width)) < 128:
                    upper = min(upper, r)
                    lower = max(lower, r)
            for c in range(ori_width):
                if min(ia[r][c][0] for r in range(upper, lower)) < 128:
                    left = min(left, c)
                    right = max(right, c)
            if any(isinstance(_, float) for _ in (upper, lower, left, right)):
                raise ValueError('Failed to find the table boundaries.')
            # add a slim border
            border_width = 15
            left = max(0, left - border_width)
            right = min(ori_width, right + border_width)
            upper = max(0, upper - border_width)
            lower = min(ori_height, lower + border_width)
            width, height = right - left, lower - upper
            # ensure aspect ratio
            max_aspect_ratio = 15
            if width / height > max_aspect_ratio:
                height = ceil(width / max_aspect_ratio)
                middle = int((upper + lower) / 2)
                upper = middle - height // 2
                lower = middle + height // 2
            elif height / width > max_aspect_ratio:
                width = ceil(height / max_aspect_ratio)
                middle = int((left + right) / 2)
                left = middle - width // 2
                right = middle + width // 2
            image = image.crop((left, upper, right, lower))
            buffer = BytesIO()
            image.save(buffer, format='png')
            return buffer
    except Exception as e:
        logger.debug('Drawing table failed', exc_info=e)
        return None


async def convert_table_to_png(table_html: str) -> Optional[BytesIO]:
    return await env.loop.run_in_executor(_matplotlib_thread_pool, _convert_table_to_png, table_html)
