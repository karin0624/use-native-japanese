"""marisa ID順の固定幅配列をArrow IPCの独立圧縮batchへ保存する。"""

from collections import OrderedDict
from pathlib import Path
from typing import Iterator

import numpy as np
import pyarrow as pa

BATCH_ROWS = 512
CACHE_BYTES = 8 * 1024 * 1024


def _columns(dtype: np.dtype) -> list[tuple[str, str | None, int | None, np.dtype]]:
    if dtype.names is None:
        return [("value", None, None, dtype)]
    result = []
    for name in dtype.names:
        field = dtype[name]
        if field.subdtype:
            base, shape = field.subdtype
            if len(shape) != 1:
                raise ValueError("配列fieldは一次元に限ります")
            result.extend((f"{name}_{i}", name, i, base) for i in range(shape[0]))
        else:
            result.append((name, name, None, field))
    return result


def _schema(dtype: np.dtype, count: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field(name, pa.from_numpy_dtype(base), nullable=False)
            for name, _, _, base in _columns(dtype)
        ],
        metadata={b"rows": str(count).encode(), b"batch_rows": str(BATCH_ROWS).encode()},
    )


def write_array(path: Path, values: np.ndarray) -> None:
    """一shardの並べ替え用mmapから一定行数ずつ書く。整数の幅と値は変えない。"""
    schema = _schema(values.dtype, len(values))
    options = pa.ipc.IpcWriteOptions(compression="zstd", use_threads=False)
    with (
        pa.OSFile(str(path), "wb") as sink,
        pa.ipc.new_file(sink, schema, options=options) as writer,
    ):
        for start in range(0, len(values), BATCH_ROWS):
            rows = values[start : start + BATCH_ROWS]
            arrays = []
            for _, field, slot, _ in _columns(values.dtype):
                column = rows if field is None else rows[field]
                if slot is not None:
                    column = column[:, slot]
                arrays.append(pa.array(column))
            writer.write_batch(pa.RecordBatch.from_arrays(arrays, schema=schema))


class BatchCache:
    """全配列で共有するLRU。保持する復号値payloadは予算を超えない。"""

    def __init__(self, budget: int = CACHE_BYTES) -> None:
        self.budget = budget
        self.bytes = 0
        self.rows: OrderedDict[tuple[Path, int], np.ndarray] = OrderedDict()

    def get(self, array: "CompressedArray", batch_id: int) -> np.ndarray:
        key = array.path, batch_id
        if key in self.rows:
            self.rows.move_to_end(key)
            return self.rows[key]
        rows = array.read_batch(batch_id)
        if rows.nbytes <= self.budget:
            while self.bytes + rows.nbytes > self.budget:
                _, previous = self.rows.popitem(last=False)
                self.bytes -= previous.nbytes
            self.rows[key] = rows
            self.bytes += rows.nbytes
        return rows

    def clear(self) -> None:
        self.rows.clear()
        self.bytes = 0


class CompressedArray:
    """IPC footerのbatch索引で必要箇所だけ復号する。全表read_allは使わない。"""

    def __init__(self, path: Path, dtype: np.dtype, count: int, cache: BatchCache) -> None:
        self.path, self.dtype, self.count, self.cache = path, dtype, count, cache
        self.source = pa.OSFile(str(path), "rb")
        try:
            self.reader = pa.ipc.open_file(
                self.source, options=pa.ipc.IpcReadOptions(use_threads=False)
            )
            if not self.reader.schema.equals(_schema(dtype, count), check_metadata=True):
                raise ValueError("圧縮配列のschemaが一致しません")
            if self.reader.num_record_batches != (count + BATCH_ROWS - 1) // BATCH_ROWS:
                raise ValueError("圧縮配列のbatch数が一致しません")
            # SHAに加えて全batchの型・null・行数を検査する。cacheへは残さない。
            for batch_id in range(self.reader.num_record_batches):
                self._batch(batch_id)
        except BaseException:
            self.source.close()
            raise

    def __len__(self) -> int:
        return self.count

    def _batch(self, batch_id: int) -> pa.RecordBatch:
        batch = self.reader.get_batch(batch_id)
        expected = min(BATCH_ROWS, self.count - batch_id * BATCH_ROWS)
        if batch.num_rows != expected or any(column.null_count for column in batch.columns):
            raise ValueError("圧縮配列の行数またはnullが不正です")
        batch.validate(full=True)
        return batch

    def read_batch(self, batch_id: int) -> np.ndarray:
        batch = self._batch(batch_id)
        rows = np.empty(batch.num_rows, dtype=self.dtype)
        for column, (_, field, slot, _) in zip(batch.columns, _columns(self.dtype), strict=True):
            values = column.to_numpy(zero_copy_only=True)
            if field is None:
                rows[:] = values
            elif slot is None:
                rows[field] = values
            else:
                rows[field][:, slot] = values
        rows.flags.writeable = False
        return rows

    def __getitem__(self, item: int):
        if not 0 <= item < self.count:
            raise IndexError("圧縮配列のIDが範囲外です")
        batch_id, offset = divmod(item, BATCH_ROWS)
        return self.cache.get(self, batch_id)[offset]

    def iter_slice(self, start: int, stop: int) -> Iterator[np.ndarray]:
        """候補postingをbatch境界で区切り、全候補を一度に展開しない。"""
        if not 0 <= start <= stop <= self.count:
            raise ValueError("候補postingの範囲が不正です")
        while start < stop:
            batch_id, offset = divmod(start, BATCH_ROWS)
            rows = self.cache.get(self, batch_id)
            length = min(len(rows) - offset, stop - start)
            yield rows[offset : offset + length]
            start += length

    def close(self) -> None:
        self.source.close()
