import io
import openpyxl
from struct import unpack


def patch_xlrd():
    try:
        import xlrd.book

        def patched_unpack_SST_table(datatab, nstrings):
            datainx = 0
            ndatas = len(datatab)
            if not ndatas:
                return [], {}
            data = datatab[0]
            datalen = len(data)
            pos = 8
            strings = []
            richtext_runs = {}

            def next_chunk():
                """Move to the next SST fragment, returning False at EOF."""
                nonlocal datainx, data, datalen, pos
                next_index = datainx + 1
                if next_index < 0 or next_index >= ndatas:
                    return False
                datainx = next_index
                data = datatab[datainx]
                datalen = len(data)
                pos = 1  # SST continuation fragments start with option flags.
                if pos >= datalen:
                    return False
                return True

            for _ in range(nstrings):
                while pos + 3 > datalen:
                    if not next_chunk():
                        return strings + [''] * (nstrings - len(strings)), richtext_runs
                nchars = unpack('<H', data[pos:pos + 2])[0]
                pos += 2
                flags = data[pos]
                pos += 1
                is_unicode = bool(flags & 1)
                has_rich = bool(flags & 8)
                has_ext = bool(flags & 4)
                rich_runs = 0
                ext_size = 0
                if has_rich:
                    rich_runs = unpack('<H', data[pos:pos + 2])[0]
                    pos += 2
                if has_ext:
                    ext_size = unpack('<I', data[pos:pos + 4])[0]
                    pos += 4
                acc = ''
                while len(acc) < nchars:
                    width = 2 if is_unicode else 1
                    available = (datalen - pos) // width
                    if available <= 0:
                        if not next_chunk():
                            break
                        is_unicode = bool(data[pos] & 1)
                        pos += 1
                        continue
                    take = min(available, nchars - len(acc))
                    chunk = data[pos:pos + take * width]
                    acc += chunk.decode('utf_16_le' if is_unicode else 'latin1', errors='replace')
                    pos += take * width
                strings.append(acc)
                if rich_runs or ext_size:
                    pos += rich_runs * 4 + ext_size
            if len(strings) < nstrings:
                strings.extend([''] * (nstrings - len(strings)))
            return strings, richtext_runs

        xlrd.book.unpack_SST_table = patched_unpack_SST_table
    except Exception:
        pass


patch_xlrd()


def read_sheet_rows(file_bytes: bytes, filename: str) -> list[list]:
    """Возвращает строки первого листа как списки значений ячеек."""
    rows = []
    if filename.lower().endswith('.xlsx'):
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            rows.append([cell if cell is not None else '' for cell in row])
        return rows
    if filename.lower().endswith('.xls'):
        import xlrd
        wb = xlrd.open_workbook(file_contents=file_bytes)
        ws = wb.sheet_by_index(0)
        for r in range(ws.nrows):
            rows.append([ws.cell_value(r, c) for c in range(ws.ncols)])
        return rows
    raise ValueError('Поддерживаются файлы .xlsx и .xls')
