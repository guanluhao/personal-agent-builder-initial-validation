"""本项目独立实现的DDS读写；不是原作者缺失的functions4IO。

依据原WriteBin.m的data(:)/float及WriteDDS.m的三维头格式实现。
模型数组约定(z,y,x)，记录数组约定(time,receiver,shot)，首轴最快。
"""
from pathlib import Path
import math
import numpy as np

AXES = ('z', 'XLINE', 'SUBLINE')

def read_header(path):
    path = Path(path).resolve()
    fields = dict(line.split('=', 1) for line in path.read_text(encoding='utf-8-sig').splitlines() if '=' in line)
    fields = {k.strip():v.strip() for k,v in fields.items()}
    if fields.get('format') != 'fcubex':
        raise ValueError(f'未知DDS格式：{path}')
    shape = tuple(int(fields['size.'+a]) for a in AXES)
    origin = tuple(float(fields['origin.'+a]) for a in AXES)
    spacing = tuple(float(fields['delta.'+a]) for a in AXES)
    if any(n < 1 for n in shape) or not np.isfinite(origin).all() or not np.isfinite(spacing).all() or min(spacing) <= 0:
        raise ValueError(f'DDS尺寸/坐标无效：{path}')
    binary = Path(fields['data'])
    if not binary.is_absolute():
        binary = path.parent/binary
    binary = binary.resolve()
    if binary.stat().st_size != math.prod(shape)*4:
        raise ValueError(f'数据尺寸不匹配：{path}；期望{shape}个float32，实际{binary.stat().st_size}字节')
    return {'header':path, 'binary':binary, 'shape':shape, 'origin':origin, 'spacing':spacing}

def read(path):
    meta = read_header(path)
    array = np.fromfile(meta['binary'],dtype='<f4').reshape(meta['shape'],order='F')
    return array, meta

def write(path, array, origin=(0,0,0), spacing=(1,1,1)):
    path = Path(path)
    array = np.asarray(array)
    if array.ndim != 3 or min(array.shape) < 1 or not np.isfinite(array).all():
        raise ValueError('DDS数组必须是有限数值的三维数组。')
    if len(origin) != 3 or len(spacing) != 3 or min(spacing) <= 0 or not np.isfinite([origin,spacing]).all():
        raise ValueError('原点与正采样间隔需要各3项。')
    if np.max(np.abs(array)) > np.finfo(np.float32).max:
        raise ValueError('结果超出float32范围，不能写入；请检查计算稳定性。')
    path.parent.mkdir(parents=True,exist_ok=True)
    binary = path.with_name(path.name+'_@')
    if path.exists() or binary.exists():
        raise FileExistsError(f'拒绝覆盖已有数据：{path}')
    array.astype('<f4').ravel(order='F').tofile(binary)
    lines=['tomde_format=1','format=fcubex','axis=z XLINE SUBLINE']
    for i,axis in enumerate(AXES):
        lines += [f'size.{axis}={array.shape[i]}',f'origin.{axis}={origin[i]:.12g}',f'delta.{axis}={spacing[i]:.12g}']
    lines += ['data='+binary.name]
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return path
