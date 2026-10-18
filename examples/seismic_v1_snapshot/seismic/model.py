from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import numpy as np
from . import dds


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _xyz3(value, name, positive=False):
    a = np.asarray(value, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all() or (positive and np.any(a <= 0)):
        suffix = '且都大于0' if positive else ''
        raise ValueError(f'{name}必须是3个有限数值组成的[x,y,z]列表{suffix}。')
    return a


def _grid_from_xyz(grid, engine):
    """把面向用户的XYZ米制网格，转换成求解器内部的ZYX网格。"""
    origin_xyz = _xyz3(grid['origin_xyz_m'], 'origin_xyz_m')
    end_xyz = _xyz3(grid['end_xyz_m'], 'end_xyz_m')
    spacing_xyz = _xyz3(grid['spacing_xyz_m'], 'spacing_xyz_m', positive=True)
    span = end_xyz - origin_xyz
    if np.any(span < 0):
        raise ValueError('end_xyz_m每个方向都不能小于origin_xyz_m。')

    intervals = span / spacing_xyz
    rounded = np.rint(intervals).astype(int)
    if not np.allclose(intervals, rounded, atol=1e-7):
        raise ValueError('模型范围必须能被网格间距整除；请调整end_xyz_m或spacing_xyz_m。')
    shape_xyz = rounded + 1

    if engine == 'acoustic2d':
        if shape_xyz[1] != 1 or min(shape_xyz[0], shape_xyz[2]) < 5:
            raise ValueError('二维模型要求y起点等于y终点，并且x、z方向都至少有5个网格点。')
    elif np.any(shape_xyz < 3):
        raise ValueError('三维模型的x、y、z方向都至少需要3个网格点。')

    grid['shape_zyx'] = shape_xyz[::-1].astype(int).tolist()
    grid['origin_zyx_m'] = origin_xyz[::-1].tolist()
    grid['spacing_zyx_m'] = spacing_xyz[::-1].tolist()


def indices(xyz, shape, origin, spacing, require_on_grid=False):
    """将XYZ米制坐标转换为ZYX索引；默认吸附到最近网格点。"""
    xyz = np.asarray(xyz, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not np.isfinite(xyz).all():
        raise ValueError('坐标必须是由[x,y,z]组成的二维列表。')
    shape = np.asarray(shape, dtype=int)
    origin = np.asarray(origin, dtype=float)
    spacing = np.asarray(spacing, dtype=float)
    fractional = (xyz[:, ::-1] - origin) / spacing
    index = np.rint(fractional).astype(int)
    if np.any(index < 0) or np.any(index >= shape):
        end_xyz = (origin + (shape - 1) * spacing)[::-1]
        raise ValueError(f'物理坐标超出模型范围，XYZ最大坐标为{end_xyz.tolist()} m。')
    if require_on_grid and not np.allclose(fractional, index, atol=1e-6):
        raise ValueError('坐标没有落在网格点上。若希望自动吸附，请不要启用require_on_grid。')
    return index


def _index_to_xyz(index_zyx, origin_zyx, spacing_zyx):
    return (np.asarray(origin_zyx) + np.asarray(index_zyx) * np.asarray(spacing_zyx))[::-1]


def _physical_bounds_to_indices(bounds_xyz, shape, origin_zyx, spacing_zyx):
    """把XYZ物理边界转换为右端不包含的ZYX切片，用户不需要处理加1减1。"""
    bounds = np.asarray(bounds_xyz, dtype=float)
    if bounds.shape != (3, 2) or not np.isfinite(bounds).all() or np.any(bounds[:, 1] < bounds[:, 0]):
        raise ValueError('bounds_xyz_m必须为[[xmin,xmax],[ymin,ymax],[zmin,zmax]]。')
    origin_xyz = np.asarray(origin_zyx, dtype=float)[::-1]
    spacing_xyz = np.asarray(spacing_zyx, dtype=float)[::-1]
    shape_xyz = np.asarray(shape, dtype=int)[::-1]
    converted_xyz = []
    for axis, ((low, high), o, d, n) in enumerate(zip(bounds, origin_xyz, spacing_xyz, shape_xyz)):
        if math.isclose(low, high, abs_tol=1e-9):
            # 二维模型的y=[0,0]或真正的物理点：取最近的一个网格点。
            i = int(round((low - o) / d))
            start, stop = i, i + 1
        else:
            # 物理区间按[low, high)采样；60 m直接写60，无需人工写61。
            start = int(math.ceil((low - o) / d - 1e-9))
            stop = int(math.ceil((high - o) / d - 1e-9))
        if not 0 <= start < stop <= n:
            axis_name = 'xyz'[axis]
            raise ValueError(f'区域的{axis_name}边界超出模型或未覆盖任何网格点。')
        converted_xyz.append([start, stop])
    return converted_xyz[::-1]


def _normalize_region(region, shape, origin, spacing):
    r = deepcopy(region)
    physical_keys = [k for k in ('bounds_xyz_m', 'point_xyz_m', 'center_xyz_m') if k in r]
    if len(physical_keys) > 1:
        raise ValueError('同一个区域只能使用bounds_xyz_m、point_xyz_m或center_xyz_m中的一种。')
    if 'bounds_xyz_m' in r:
        r['index_bounds_zyx'] = _physical_bounds_to_indices(r['bounds_xyz_m'], shape, origin, spacing)
    elif 'point_xyz_m' in r:
        point = _xyz3(r['point_xyz_m'], 'point_xyz_m')
        idx = indices(point[None, :], shape, origin, spacing)[0]
        r['index_bounds_zyx'] = [[int(i), int(i) + 1] for i in idx]
    elif 'center_xyz_m' in r:
        center = _xyz3(r['center_xyz_m'], 'center_xyz_m')
        size = _xyz3(r.get('size_xyz_m'), 'size_xyz_m', positive=True)
        r['index_bounds_zyx'] = _physical_bounds_to_indices(
            np.column_stack((center - size / 2, center + size / 2)), shape, origin, spacing)
    elif 'index_bounds_zyx' not in r:
        raise ValueError('区域必须提供XYZ物理位置。')
    return r


def normalize_config(raw):
    """生成内部标准配置。新配置只写XYZ；旧ZYX配置仍可继续运行。"""
    config = deepcopy(raw)
    grid = config['grid']
    new_grid = all(k in grid for k in ('origin_xyz_m', 'end_xyz_m', 'spacing_xyz_m'))
    if new_grid:
        _grid_from_xyz(grid, config.get('engine'))
    elif not all(k in grid for k in ('shape_zyx', 'origin_zyx_m', 'spacing_zyx_m')):
        raise ValueError('grid须使用新的XYZ米制格式，或使用完整的旧ZYX格式。')
    else:
        # 旧实验自动补齐XYZ字段，确保新的绘图和后续模块仍能读取。
        shape_zyx = np.asarray(grid['shape_zyx'], dtype=int)
        origin_zyx = np.asarray(grid['origin_zyx_m'], dtype=float)
        spacing_zyx = np.asarray(grid['spacing_zyx_m'], dtype=float)
        grid['origin_xyz_m'] = origin_zyx[::-1].tolist()
        grid['spacing_xyz_m'] = spacing_zyx[::-1].tolist()
        grid['end_xyz_m'] = (origin_zyx + (shape_zyx - 1) * spacing_zyx)[::-1].tolist()

    shape = tuple(grid['shape_zyx'])
    origin = tuple(grid['origin_zyx_m'])
    spacing = tuple(grid['spacing_zyx_m'])
    config['regions'] = [_normalize_region(r, shape, origin, spacing) for r in config.get('regions', [])]

    imaging = config.get('imaging')
    if imaging is not None and 'angle_x_positions_m' in imaging:
        x0 = origin[2]
        dx = spacing[2]
        positions_m = np.asarray(imaging['angle_x_positions_m'], dtype=float)
        if positions_m.ndim != 1 or not len(positions_m) or not np.isfinite(positions_m).all():
            raise ValueError('angle_x_positions_m必须是不为空的x坐标列表，单位为m。')
        x_indices = np.rint((positions_m - x0) / dx).astype(int)
        if np.any(x_indices < 0) or np.any(x_indices >= shape[2]):
            raise ValueError('角度道集位置超出模型x范围。')
        imaging['angle_x_indices'] = x_indices.tolist()

    simulation = config.get('simulation', {})
    if 'pml_thickness_m' in simulation:
        thickness = float(simulation['pml_thickness_m'])
        if not math.isfinite(thickness) or thickness <= 0:
            raise ValueError('pml_thickness_m必须是正数，单位为m。')
        # 当前求解器各方向共用同一个PML层数。按最小有效空间间距换算，
        # 可保证每个计算方向的PML物理厚度都不小于用户给定值。
        effective_spacing = [spacing[0], spacing[2]] if config.get('engine') == 'acoustic2d' else list(spacing)
        simulation['pml_cells'] = int(math.ceil(thickness / min(effective_spacing) - 1e-9))

    config['_internal'] = {
        'note': 'shape_zyx、index_bounds_zyx、angle_x_indices和pml_cells由程序自动生成，仅供后续计算。',
        'input_axis_order': 'xyz',
        'solver_array_order': 'zyx'
    }
    return config


def ricker(frequency, dt, duration, delay):
    if min(frequency, dt, duration) <= 0 or not 0 <= delay <= duration:
        raise ValueError('子波频率、间隔、时长需为正，延迟应在子波时段内。')
    steps = duration / dt
    if not math.isclose(steps, round(steps), abs_tol=1e-7):
        raise ValueError('子波时长应是dt的整数倍。')
    t = np.arange(round(steps) + 1) * dt
    beta = (np.pi * frequency * (t - delay)) ** 2
    wave = (1 - 2 * beta) * np.exp(-beta)
    peak = np.max(wave)
    if peak <= 0:
        raise ValueError('子波主峰未被采样，请调整dt和延迟。')
    return wave / peak


def points(spec):
    if isinstance(spec, list):
        result = np.asarray(spec, dtype=float)
    elif 'segments' in spec:
        parts = [points(part) for part in spec['segments']]
        if not parts:
            raise ValueError('segments不能为空。')
        result = np.vstack(parts)
    else:
        start = np.asarray(spec['start_xyz'], dtype=float)
        step = np.asarray(spec['step_xyz'], dtype=float)
        count = spec['count']
        if start.shape != (3,) or step.shape != (3,):
            raise ValueError('start_xyz和step_xyz都应为[x,y,z]。')
        if not isinstance(count, int) or count < 1:
            raise ValueError('炮/检波点数量应为正整数。')
        result = start + np.arange(count)[:, None] * step
    if result.ndim != 2 or result.shape[1] != 3 or not len(result) or not np.isfinite(result).all():
        raise ValueError('炮检坐标须为非空的[x,y,z]列表。')
    return result


def _snap_from_zero_vp(xyz, vp, origin, spacing, max_cells=1.01):
    """物理坐标恰在零速隧道壁时，自动移到最近的非零计算节点。"""
    idx = indices(xyz, vp.shape, origin, spacing)
    active = np.argwhere(vp > 0)
    if not len(active):
        raise ValueError('模型中不存在vp>0的传播区域。')
    result = []
    max_distance = max_cells * float(np.max(spacing))
    for requested, current in zip(xyz, idx):
        chosen = current
        if vp[tuple(current)] <= 0:
            delta_m = (active - current) * np.asarray(spacing)
            chosen = active[int(np.argmin(np.sum(delta_m ** 2, axis=1)))]
            distance = float(np.linalg.norm((chosen - current) * np.asarray(spacing)))
            if distance > max_distance + 1e-9:
                raise ValueError(f'炮检点{requested.tolist()}位于零速区内部，最近传播网格点距离{distance:g} m。')
        result.append(_index_to_xyz(chosen, origin, spacing))
    return np.asarray(result)


def build(config_path, case):
    config = normalize_config(load_json(config_path))
    shape = tuple(config['grid']['shape_zyx'])
    origin = tuple(config['grid']['origin_zyx_m'])
    spacing = tuple(config['grid']['spacing_zyx_m'])
    if len(shape) != 3 or any(not isinstance(n, int) for n in shape):
        raise ValueError('内部shape_zyx需要3个整数。')
    if len(origin) != 3 or len(spacing) != 3 or min(spacing) <= 0 or not np.isfinite([origin, spacing]).all():
        raise ValueError('网格原点与间隔无效。')

    bg = config['background']
    arrays = {key: np.full(shape, float(bg[key]), dtype=np.float32) for key in ('vp', 'vs', 'rho')}
    for region in config.get('regions', []):
        if not region.get('enabled', True):
            continue
        bounds = region['index_bounds_zyx']
        if len(bounds) != 3 or any(len(b) != 2 or any(not isinstance(x, int) for x in b)
                or not 0 <= b[0] < b[1] <= shape[i] for i, b in enumerate(bounds)):
            raise ValueError('程序自动生成的区域索引无效。')
        slices = tuple(slice(*b) for b in bounds)
        for key in arrays:
            if key in region:
                arrays[key][slices] = float(region[key])
    if (not all(np.isfinite(a).all() for a in arrays.values()) or arrays['rho'].min() <= 0
            or arrays['vp'].min() < 0 or arrays['vs'].min() < 0):
        raise ValueError('模型必须有限，rho>0，vp/vs>=0。')

    requested_shots = points(config['geometry']['shots_xyz_m'])
    requested_receivers = points(config['geometry']['receivers_xyz_m'])
    shots = _snap_from_zero_vp(requested_shots, arrays['vp'], origin, spacing)
    receivers = _snap_from_zero_vp(requested_receivers, arrays['vp'], origin, spacing)
    config['geometry']['requested_shots_xyz_m'] = requested_shots.tolist()
    config['geometry']['requested_receivers_xyz_m'] = requested_receivers.tolist()
    config['geometry']['computational_shots_xyz_m'] = shots.tolist()
    config['geometry']['computational_receivers_xyz_m'] = receivers.tolist()

    geom = np.zeros((13, len(receivers), len(shots)), dtype=np.float32)
    geom[2] = shots[:, 2][None, :]
    geom[5] = receivers[:, 2, None]
    geom[6] = 1
    geom[7] = shots[:, 0][None, :]
    geom[8] = shots[:, 1][None, :]
    geom[9] = receivers[:, 0, None]
    geom[10] = receivers[:, 1, None]

    wav = config['wavelet']
    dt = float(config['simulation']['dt_s'])
    wave = ricker(float(wav['frequency_hz']), dt, float(wav['duration_s']), float(wav['delay_s']))
    case = Path(case).resolve()
    case.mkdir(parents=True, exist_ok=False)
    for key, array in arrays.items():
        dds.write(case / key, array, origin, spacing)
    dds.write(case / 'geomz0', geom, (1, 1, 1), (1, 1, 1))
    dds.write(case / 'wavelet', wave[:, None, None], (0, 1, 1), (dt, 1, 1))
    save_json(case / 'case.json', config)
    check(case)
    return case


def check(case):
    case = Path(case).resolve()
    config = load_json(case / 'case.json')
    data = {key: dds.read(case / key) for key in ('vp', 'vs', 'rho', 'geomz0', 'wavelet')}
    vp, meta = data['vp']
    rho = data['rho'][0]
    for key in ('vs', 'rho'):
        if any(data[key][1][k] != meta[k] for k in ('shape', 'origin', 'spacing')):
            raise ValueError('模型网格不一致。')
    if not all(np.isfinite(pair[0]).all() for pair in data.values()) or vp.min() < 0 or rho.min() <= 0 or vp.max() <= 0:
        raise ValueError('存在无效模型或数据数值。')
    grid = config['grid']
    if (tuple(grid['shape_zyx']) != meta['shape'] or tuple(grid['origin_zyx_m']) != meta['origin']
            or tuple(grid['spacing_zyx_m']) != meta['spacing']):
        raise ValueError('case.json网格与DDS文件不一致，请通过build重建案例。')

    simulation = config['simulation']
    dt = float(simulation['dt_s'])
    tmax = float(simulation['tmax_s'])
    steps = tmax / dt if dt > 0 else float('inf')
    if not math.isfinite(steps) or steps < 1 or not math.isclose(steps, round(steps), abs_tol=1e-7):
        raise ValueError('Tmax必须为正，且是dt的整数倍。')
    bd = simulation['pml_cells']
    fm = simulation['pml_frequency_hz']
    if not isinstance(bd, int) or bd < 3 or not math.isfinite(fm) or fm <= 0:
        raise ValueError('PML厚度需为至少3的整数，PML频率需为正。')
    cfl = float(vp.max()) * dt * np.sqrt(np.sum(1 / np.asarray(meta['spacing']) ** 2))
    if cfl >= 0.9:
        raise ValueError(f'基础CFL筛查未通过({cfl:.4f}>=0.9)，请减小dt。')

    geom = data['geomz0'][0]
    if geom.shape[0] != 13:
        raise ValueError('几何首维必须是13。')
    selected = simulation['shot_ids']
    if (not selected or any(not isinstance(n, int) or not 1 <= n <= geom.shape[2] for n in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('shot_ids为不重复、从1起算的有效炮号列表。')
    source = config.get('source', {'kind': 'pressure'})
    if not isinstance(source, dict) or source.get('kind', 'pressure') not in ('pressure', 'force'):
        raise ValueError('source.kind只能是pressure或force。')
    if source.get('kind') == 'force':
        directions = np.asarray(source.get('directions_xyz'), dtype=float)
        if (directions.shape != (geom.shape[2], 3) or not np.isfinite(directions).all()
                or not np.allclose(directions[:, 1], 0, atol=1e-12)
                or not np.allclose(np.linalg.norm(directions[:, [0, 2]], axis=1), 1, atol=1e-6)):
            raise ValueError('二维方向力震源需要为每个炮点设置单位向量source.directions_xyz=[x,0,z]；不支持Y分量。')
        amplitude = source.get('amplitude', 1.0)
        if not isinstance(amplitude, (int, float)) or not math.isfinite(amplitude) or amplitude == 0:
            raise ValueError('source.amplitude必须是非零有限数值。')
    for sid in selected:
        g = geom[:, :, sid - 1]
        shot_xyz = g[[7, 8, 2], :].T
        if not np.all(shot_xyz == shot_xyz[0]):
            raise ValueError('一炮的炮点坐标必须一致。')
        indices(shot_xyz, meta['shape'], meta['origin'], meta['spacing'])
        indices(g[[9, 10, 5], :].T, meta['shape'], meta['origin'], meta['spacing'])

    wave, wmeta = data['wavelet']
    if wave.shape[1:] != (1, 1) or wmeta['origin'][0] != 0 or not math.isclose(wmeta['spacing'][0], dt):
        raise ValueError('子波必须为从0起、间隔与dt一致的一维时间序列。')
    power = simulation.get('wave_power', 0)
    if not isinstance(power, int) or power < 0:
        raise ValueError('wave_power仅支持非负整数阶。')
    warnings = []
    if vp.min() == 0:
        warnings.append('存在vp=0区域；炮检点若落在其边界，已自动吸附到最近的vp>0计算节点。')
    if source.get('kind') == 'force':
        positive_vp = vp[vp > 0]
        frequency = float(config['wavelet']['frequency_hz'])
        if frequency > 0 and positive_vp.min() / (frequency * max(meta['spacing'][0], meta['spacing'][2])) < 8:
            warnings.append('最低速度区域在主频下不足8个网格点/波长；方向力正演可能出现严重数值频散，请调整频率或网格。')
    padded = np.asarray(meta['shape']) + 2 * bd
    summary = {
        'shape_zyx': list(vp.shape), 'shots': selected, 'receivers': geom.shape[1],
        'nt': round(steps) + 1, 'dt_s': dt, 'cfl_screen': float(cfl),
        'rough_memory_gib_per_worker': float(np.prod(padded) * 240 / 1024 ** 3),
        'warnings': warnings
    }
    return config, data, summary


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
