"""服务器无桌面绘图：只保存PNG，不调用show。"""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from . import dds
from .model import load_json,points

def save_png_svg(fig, png_path, dpi=150):
    """同一张图同时保存为 PNG 和 SVG。"""
    png_path = Path(png_path)
    fig.savefig(png_path, dpi=dpi)
    fig.savefig(
        png_path.with_suffix('.svg'),
        format='svg',
        dpi=600
    )
    plt.close(fig)

def plot_inputs(case,output):
    case=Path(case); output=Path(output); output.mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(11,4),constrained_layout=True)
    for ax,key in zip(axes,('vp','rho')):
        data,meta=dds.read(case/key); nz,ny,nx=data.shape; j=ny//2
        dz,dy,dx=meta['spacing']; z0,y0,x0=meta['origin']
        im=ax.imshow(data[:,j,:],aspect='auto',extent=[x0-.5*dx,x0+(nx-.5)*dx,z0+(nz-.5)*dz,z0-.5*dz])
        ax.set(title=f'{key}: y={y0+j*dy:g}',xlabel='x / m',ylabel='z / m');fig.colorbar(im,ax=ax)
    save_png_svg(fig, output / 'model.png', dpi=150)
    geom,_=dds.read(case/'geomz0');config=load_json(case/'case.json')
    if config.get('engine')=='acoustic2d':
        # 第一张图显示用户输入的物理坐标，第二张显示求解器实际采用的网格节点。
        # 两张图使用同一个局部范围，只展示隧道和观测系统，便于直接比较。
        from matplotlib.patches import Rectangle
        requested_shots=np.asarray(config['geometry'].get('requested_shots_xyz_m',points(config['geometry']['shots_xyz_m'])))
        requested_receivers=np.asarray(config['geometry'].get('requested_receivers_xyz_m',points(config['geometry']['receivers_xyz_m'])))
        region_bounds=[]
        for region in config.get('regions',[]):
            if not region.get('enabled',True) or 'bounds_xyz_m' not in region:continue
            if 'bounds_xyz_m' in region:
                (xmin,xmax),_,(zmin,zmax)=region['bounds_xyz_m']
            else:
                (zlo,zhi),_,(xlo,xhi)=region['index_bounds_zyx']
                dz,_,dx=config['grid']['spacing_zyx_m'];oz,_,ox=config['grid']['origin_zyx_m']
                xmin,xmax=ox+xlo*dx,ox+xhi*dx;zmin,zmax=oz+zlo*dz,oz+zhi*dz
            region_bounds.append((xmin,xmax,zmin,zmax,region.get('role','region')))

        computational_shots=np.column_stack((geom[7,0,:],geom[8,0,:],geom[2,0,:]))
        computational_receivers=np.column_stack((geom[9,:,0],geom[10,:,0],geom[5,:,0]))
        bounds_x=np.asarray([[b[0],b[1]] for b in region_bounds]).ravel() if region_bounds else np.array([])
        bounds_z=np.asarray([[b[2],b[3]] for b in region_bounds]).ravel() if region_bounds else np.array([])
        all_x=np.concatenate((requested_shots[:,0],requested_receivers[:,0],computational_shots[:,0],computational_receivers[:,0],bounds_x))
        all_z=np.concatenate((requested_shots[:,2],requested_receivers[:,2],computational_shots[:,2],computational_receivers[:,2],bounds_z))
        xpad=max(2.0,.05*float(np.ptp(all_x)));zpad=max(2.0,.05*float(np.ptp(all_z)))
        limits=(float(all_x.min()-xpad),float(all_x.max()+xpad),float(all_z.min()-zpad),float(all_z.max()+zpad))

                # 根据自动生成的网格索引，计算零速度网格单元的实际显示边缘。
        # 数组中的坐标代表网格单元中心，所以边缘需要向两侧延伸半个网格。
        dz, _, dx = config['grid']['spacing_zyx_m']
        oz, _, ox = config['grid']['origin_zyx_m']

        grid_tunnel_bounds = []

        for region in config.get('regions', []):
            if not region.get('enabled', True) or 'bounds_xyz_m' not in region:
                continue

            (zlo, zhi), _, (xlo, xhi) = region['index_bounds_zyx']

            grid_xmin = ox + (xlo - 0.5) * dx
            grid_xmax = ox + (xhi - 0.5) * dx
            grid_zmin = oz + (zlo - 0.5) * dz
            grid_zmax = oz + (zhi - 0.5) * dz

            grid_tunnel_bounds.append(
                (grid_xmin, grid_xmax, grid_zmin, grid_zmax,region.get('role','region'))
            )

        def geometry_figure(
            shots,
            receivers,
            bounds,
            title,
            filename,
            view_limits=None,
            label_shots=False,
            show_force_arrows=True
        ):
            fig, ax = plt.subplots(
                figsize=(10, 5.5) if label_shots else (12, 4.8)
            )
            fig.subplots_adjust(right=0.77,top=0.88,bottom=0.15)

            shown=set()
            for xmin, xmax, zmin, zmax, role in bounds:
                target=role=='target'
                label='Target cavity' if target else 'Fixed structure'
                ax.add_patch(
                    Rectangle(
                        (xmin, zmin),
                        xmax - xmin,
                        zmax - zmin,
                        facecolor='0.82' if target else '0.70',
                        edgecolor='tab:orange' if target else 'black',
                        linestyle='--' if target else '-',
                        linewidth=1.2,
                        label=label if label not in shown else None
                    )
                )
                shown.add(label)

            ax.scatter(
                shots[:, 0],
                shots[:, 2],
                c='red',
                marker='*',
                s=75,
                label='Sources',
                zorder=3
            )
            if label_shots:
                for j,(x,_,z) in enumerate(shots):
                    ax.annotate(f'S{j+1}',(x,z),xytext=(5,-13-11*(j%2)),
                                textcoords='offset points',fontsize=9,color='darkred',zorder=5)

            source=config.get('source',{})
            if source.get('kind')=='force' and show_force_arrows:
                directions=np.asarray(source['directions_xyz'],dtype=float)
                ax.quiver(shots[:,0],shots[:,2],directions[:,0],directions[:,2],
                          color='darkred',angles='xy',scale_units='xy',scale=0.35,
                          width=0.005,label='Force direction',zorder=4)

            ax.scatter(
                receivers[:, 0],
                receivers[:, 2],
                c='blue',
                s=18,
                label='Receivers',
                zorder=3
            )

            xmin, xmax, zmin, zmax = view_limits or limits

            ax.set(
                xlim=(xmin, xmax),
                ylim=(zmax, zmin),
                xlabel='x / m',
                ylabel='z / m',
                title=title
            )

            ax.set_aspect('equal', adjustable='box')
            ax.grid(alpha=0.2)
            ax.legend(loc='upper left',bbox_to_anchor=(1.02,1),borderaxespad=0)

            save_png_svg(fig, output / filename, dpi=160)

        geometry_figure(
            requested_shots,
            requested_receivers,
            region_bounds,
            'Physical input: tunnel and acquisition',
            'geometry.png'
        )

        if config.get('source',{}).get('kind')=='force':
            local_limits=(float(min(requested_receivers[:,0].min(),requested_shots[:,0].min())-3),
                          float(max(requested_shots[:,0].max(),60)+4),
                          float(min(requested_receivers[:,2].min(),requested_shots[:,2].min(),50)-5),
                          float(max(requested_receivers[:,2].max(),requested_shots[:,2].max(),60)+5))
            geometry_figure(requested_shots,requested_receivers,region_bounds,
                            'Near-face acquisition: physical coordinates',
                            'geometry_local.png',local_limits,True)

        geometry_figure(
            computational_shots,
            computational_receivers,
            grid_tunnel_bounds,
            'Grid-adjusted model used by solver',
            'geometry_grid_nodes.png',
            show_force_arrows=False
        )
        if config.get('source',{}).get('kind')=='force':
            geometry_figure(computational_shots,computational_receivers,grid_tunnel_bounds,
                            'Near-face acquisition: solver grid nodes',
                            'geometry_grid_nodes_local.png',local_limits,True,False)

    else:
        fig=plt.figure(figsize=(7,5));ax=fig.add_subplot(projection='3d')
        ax.scatter(geom[7,0,:],geom[8,0,:],geom[2,0,:],c='red',marker='*',label='Sources')
        ax.scatter(geom[9,:,0],geom[10,:,0],geom[5,:,0],c='blue',s=12,label='Receivers (shot 1)')
        ax.set(xlabel='x / m',ylabel='y / m',zlabel='z / m');ax.invert_zaxis();ax.legend()
        fig.tight_layout()
        save_png_svg(fig, output / 'geometry.png', dpi=150)
    wave,meta=dds.read(case/'wavelet');w=wave[:,0,0];dt=meta['spacing'][0]
    fig,axes=plt.subplots(1,2,figsize=(10,3.5),constrained_layout=True)
    axes[0].plot(np.arange(len(w))*dt,w);axes[0].set(xlabel='Time / s',ylabel='Amplitude',title='Source wavelet')
    nfft=max(4096,2**int(np.ceil(np.log2(len(w)))))
    f=np.fft.rfftfreq(nfft,dt);spectrum=np.abs(np.fft.rfft(w,n=nfft))
    axes[1].plot(f,spectrum); axes[1].set(xlabel='Frequency / Hz',ylabel='Amplitude',title='Wavelet spectrum')
    axes[1].set_xlim(0,min(1/(2*dt),4*config['wavelet']['frequency_hz']))
    save_png_svg(fig, output / 'wavelet.png', dpi=150)

def plot_records(run,shot=1):
    run=Path(run);meta_run=load_json(run/'run.json')
    if meta_run['status']!='complete':raise ValueError('该运行未完成，不能当作有效结果绘图。')
    output=run/'图件';output.mkdir(exist_ok=True)
    fig,axes=plt.subplots(2,2,figsize=(11,7),constrained_layout=True)
    for ax,key in zip(axes.ravel(),('p','x','y','z')):
        data,meta=dds.read(run/('record'+key))
        if not 1<=shot<=data.shape[2]:raise ValueError('shot为输出中的炮序号，从1开始。')
        view=data[:,:,shot-1];nt,nrec=view.shape;dt=meta['spacing'][0]
        scale=float(np.max(np.abs(view))) or 1.0
        im=ax.imshow(view,cmap='seismic',vmin=-scale,vmax=scale,aspect='auto',extent=[.5,nrec+.5,(nt-.5)*dt,-.5*dt])
        ax.set(xlabel='Receiver index',ylabel='Time / s',title=f'record{key}, output shot {shot}')
        fig.colorbar(im,ax=ax)
    save_png_svg(
    fig,
    output / f'records_shot{shot:03d}.png',
    dpi=150
    )
    data,meta=dds.read(run/'recordp');mid=data.shape[1]//2;dt=meta['spacing'][0]
    fig,ax=plt.subplots(figsize=(8,3),constrained_layout=True)
    ax.plot(np.arange(data.shape[0])*dt,data[:,mid,shot-1]);ax.set(xlabel='Time / s',ylabel='Pressure amplitude',title=f'Receiver {mid+1}')
    save_png_svg(
    fig,
    output / f'trace_shot{shot:03d}.png',
    dpi=150
    )
    return output
