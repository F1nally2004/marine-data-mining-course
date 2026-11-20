"""Regenerate report figures from preserved experiment summaries, never rerun models.

Use --accuracy-input/--failure-input with the exported tables in the GitHub bundle
to redraw figures without the large per-window logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

METHODS=['WM_DELTA','TIMER_DELTA','PAYLOAD_BARRIER','UPSTREAM_COUNT_ONLY','FULL_ID_MANIFEST','ID_STREAM_30S','SEALED_DIFF_EXACT','SEALED_DIFF_HASH']
SHORT=['WM','TIMER','PAYLOAD','COUNT','FULL-ID','ID-30s','DIFF-E','DIFF-H']
HORIZONS=[0,60,180,600,1200,3600,7200]
COLORS=['#0072B2','#E69F00','#777777','#009E73','#56B4E9','#CC79A7','#D55E00','#6B4C9A']

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--study-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--summary-dir',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--accuracy-input',type=Path)
    parser.add_argument('--failure-input',type=Path)
    args=parser.parse_args()
    study=args.study_root.resolve()
    summary=args.summary_dir or study/'reports/canonical_matrix'
    output=args.output or study/'publication/output'
    figures,tables=output/'figures',output/'tables'
    figures.mkdir(parents=True,exist_ok=True)
    tables.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR',str(output/'.mpl-cache'))
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    plt.rcParams.update({'font.family':['Microsoft YaHei','DejaVu Sans'], 'axes.unicode_minus':False,
                         'font.size':10,'axes.titlesize':12,'axes.labelsize':10,
                         'pdf.fonttype':42,'savefig.dpi':200,'axes.spines.top':False,'axes.spines.right':False})
    runs=pd.read_csv(summary/'run_summary.csv')
    daily=pd.read_csv(summary/'daily_summary.csv')
    assert len(runs)==848 and runs.run_id.nunique()==848
    primary=runs[runs.delta_seconds.isna()|runs.delta_seconds.eq(180)]
    replay=primary[primary.phase.eq('replay')]
    provenance={'summary_hashes':{name:sha(summary/name) for name in ['run_summary.csv','daily_summary.csv','paired_date_differences_vs_count.csv']},
                'scope':'848 configurations; main replay uses delta=180 or non-delta methods; 40 runs per method',
                'uncertainty':'No confidence bars: simulation seeds are not independent traffic samples; per-date pairing displayed separately',
                'matplotlib_version':matplotlib.__version__,'window_files':{}}
    if args.accuracy_input:
        accuracy=pd.read_csv(args.accuracy_input)
    else:
        manifest=json.loads((summary/'summary_manifest.json').read_text(encoding='utf-8'))
        records=[]
        for r in replay.itertuples():
            folder=Path(manifest['resolved_run_paths'][r.run_id])
            path=folder/'window_metrics.csv'
            digest=sha(path)
            audit=json.loads((folder/'audit.json').read_text(encoding='utf-8'))
            assert digest==audit['output_sha256']['window_metrics.csv']
            provenance['window_files'][r.run_id]=digest
            data=pd.read_csv(path,usecols=[f'absolute_error_{h}' for h in HORIZONS])
            for h in HORIZONS:
                records.append({'run_id':r.run_id,'method':r.method,'horizon':h,'windows':len(data),
                                'correct_windows':int(data[f'absolute_error_{h}'].eq(0).sum()),
                                'absolute_error_sum':float(data[f'absolute_error_{h}'].sum())})
        accuracy=pd.DataFrame(records)
    accuracy.to_csv(tables/'accuracy_counts.csv',index=False,encoding='utf-8-sig')
    if args.failure_input:
        failure=pd.read_csv(args.failure_input)
    else:
        # These are the originally reported fixed-model scores, read from the preserved report.
        path=study.parent/'AIS漏计风险预实验/outputs/metrics.csv'
        data=pd.read_csv(path)
        # Keep only fields that actually exist; inspect rather than guess their names.
        label_col='scenario' if 'scenario' in data else 'mechanism'
        model_col='model' if 'model' in data else 'method'
        score_col='brier'
        if not {label_col,model_col,score_col}.issubset(data.columns):
            raise ValueError(f'unsupported preserved metrics schema: {list(data.columns)}')
        failure=data.groupby([label_col,model_col],as_index=False)[score_col].mean().rename(columns={label_col:'scenario',model_col:'model'})
        provenance['failure_metrics_sha256']=sha(path)
    failure.to_csv(tables/'failure_brier.csv',index=False,encoding='utf-8-sig')
    def stats(g):
        n=g.windows.sum()
        claimed=g.ever_claimed_windows.sum()
        return {'runs':len(g),'windows':int(n),'wait':float((g.restricted_mean_wait*g.windows).sum()/n),
                'coverage':float(g.confirmed_windows.sum()/n),'false_rate':float(g.ever_false_count_windows.sum()/claimed) if claimed else np.nan,
                'false_windows':int(g.ever_false_count_windows.sum()),'claimed_windows':int(claimed),
                'network':float(g.network_bytes.mean()),'control':float(g.control_bytes.mean()),
                'invalidated_rate':float(g.invalidated_claim_windows.sum()/n)}
    aggregate=pd.DataFrame([{'method':m,**stats(replay[replay.method.eq(m)])} for m in METHODS])
    aggregate.to_csv(tables/'replay_primary.csv',index=False,encoding='utf-8-sig')
    def save(fig,name,caption):
        fig.savefig(figures/f'{name}.png',bbox_inches='tight')
        fig.savefig(figures/f'{name}.pdf',bbox_inches='tight')
        fig.savefig(figures/f'{name}.svg',bbox_inches='tight')
        plt.close(fig)
        captions[name]=caption
    def horizontal(ax,values,label,formatter=lambda v:f'{v:.1f}'):
        y=np.arange(8)
        ax.barh(y,values,color=COLORS,edgecolor='white',height=.65)
        ax.set_yticks(y,SHORT)
        ax.invert_yaxis()
        ax.set_xlabel(label)
        maximum=max(values) if max(values)>0 else 1
        ax.set_xlim(0,maximum*1.22)
        for i,v in enumerate(values):
            ax.text(v+maximum*.012,i,formatter(v),va='center',fontsize=9)
        ax.grid(axis='x',alpha=.18)
        ax.set_axisbelow(True)
    captions={}
    fig,axes=plt.subplots(1,2,figsize=(12,5.5),layout='constrained')
    horizontal(axes[0],aggregate.wait.tolist(),'限制平均确认等待 / 秒（H=7200）')
    horizontal(axes[1],(aggregate.coverage*100).tolist(),'有效确认覆盖率 / %',lambda v:f'{v:.2f}%')
    axes[0].set_title('(a) 包含未确认窗口的延迟')
    axes[1].set_title('(b) 确认覆盖率')
    fig.suptitle('图1  正常 AIS 回放：延迟与覆盖率（主 Δ=180 秒）')
    save(fig,'fig01_delay_coverage','40次19日回放/方法；每次2736窗。未确认窗口在限制平均等待中计7200秒，未作成功样本筛选。覆盖率单独展示；首次临时发布延迟均为0。无误差条，种子不是独立交通样本。')
    fig,axes=plt.subplots(1,3,figsize=(17,5.5),layout='constrained')
    grouped=accuracy.groupby(['method','horizon'])[['windows','correct_windows','absolute_error_sum']].sum()
    # Equal curves are explicitly identified in the caption rather than jittered.
    for i,m in enumerate(METHODS):
        if m in ['SEALED_DIFF_HASH','PAYLOAD_BARRIER']:
            continue
        g=grouped.loc[m].reindex(HORIZONS)
        axes[0].plot(HORIZONS,g.absolute_error_sum/g.windows,marker=['o','s','^','D','v','P','X','*'][i],
                     color=COLORS[i],label=SHORT[i],linewidth=1.5)
    axes[0].set_xscale('symlog',linthresh=60)
    axes[0].set_xticks([0,60,180,600,1200,7200],[0,60,180,600,1200,7200],rotation=40)
    axes[0].set_xlabel('窗口结束后的共同时间 / 秒')
    axes[0].set_ylabel('平均绝对计数误差 / 艘')
    axes[0].legend(fontsize=8,ncol=2)
    axes[0].grid(alpha=.2)
    values=[100*grouped.loc[(m,180),'correct_windows']/grouped.loc[(m,180),'windows'] for m in METHODS]
    horizontal(axes[1],values,'180 秒时计数精确相等的窗口 / %',lambda v:f'{v:.3f}%')
    axes[1].set_xlim(0,108)
    horizontal(axes[2],(aggregate.false_rate*100).tolist(),'曾错误确认窗口 / 曾声明窗口（%）',lambda v:f'{v:.3f}%')
    axes[0].set_title('(a) 共同时间点计数误差')
    axes[1].set_title('(b) 计数准确率（包含未确认窗口）')
    axes[2].set_title('(c) 错误确认率（历史错误保留）')
    fig.suptitle('图2  正常回放：数值准确与确认可靠性')
    save(fig,'fig02_accuracy','准确率定义为共同时间点估计计数恰等于采集并集真值的窗口比例；包含空窗及未确认窗。错误确认率以曾声明窗口为分母。PAYLOAD与COUNT误差曲线重合，DIFF-H与DIFF-E重合，重合曲线省略绘制。误差曲线为离散时间点连线，不估计两点间的过程。')
    fig,axes=plt.subplots(1,2,figsize=(12,5.5),layout='constrained')
    y=np.arange(8)
    payload=(aggregate.network-aggregate.control)/2**20
    control=aggregate.control/2**20
    axes[0].barh(y,payload,color='#bbbbbb',label='数据链路字节')
    axes[0].barh(y,control,left=payload,color=COLORS,label='控制链路字节')
    axes[0].set_yticks(y,SHORT); axes[0].invert_yaxis()
    axes[0].set_xlabel('平均双向总网络 / MiB·运行⁻¹')
    axes[0].legend(loc='lower right',fontsize=9)
    horizontal(axes[1],(aggregate.control/1024).tolist(),'平均控制网络 / KiB·运行⁻¹')
    axes[0].set_title('(a) 数据与控制分开计费'); axes[1].set_title('(b) 控制开销单独放大')
    fig.suptitle('图3  通信开销：包含头部、分帧、双向请求和重传')
    save(fig,'fig03_communication','每次运行覆盖19个原UTC日期，40次等权平均。数据/控制均为网络层模拟字节，不是Python对象大小。WM/TIMER无控制流量；控制为0不表示有精确完备性证明。MiB=2^20 B，KiB=2^10 B。')
    env=replay.groupby(['copy_probability','delay_model','scheduler','method']).restricted_mean_wait.mean().unstack('method').reindex(columns=METHODS)
    env.to_csv(tables/'scenario_latency.csv',encoding='utf-8-sig')
    fig,ax=plt.subplots(figsize=(12,5.2),layout='constrained')
    image=ax.imshow(env.to_numpy(),norm=LogNorm(),cmap='YlGnBu',aspect='auto')
    ax.set_xticks(np.arange(8),SHORT)
    ax.set_yticks(np.arange(len(env)),[f'p={p:g} / {d} / {s}' for p,d,s in env.index])
    for row in range(len(env)):
        for col in range(8):
            ax.text(col,row,f'{env.iloc[row,col]:.1f}',ha='center',va='center',fontsize=9,
                    color='white' if image.norm(env.iloc[row,col])>.62 else 'black')
    fig.colorbar(image,ax=ax,label='限制平均等待 / 秒（对数色阶）')
    ax.set_title('图4  复制率、链路和调度场景：每格5种子平均')
    save(fig,'fig04_link_scenarios','iid为独立慢包；burst为源小时级不可发送期；FIFO与控制/数据20:80的包级WFQ均共享容量和断网。p是记录复制概率，不是集合Jaccard。色阶对数，格内数值为秒。')
    pressure=primary[primary.phase.eq('pressure')]
    order=['n1000_r1','n1000_r5','n10000_r1','n10000_r5']
    ptable=pressure.groupby(['case_id','method']).restricted_mean_wait.mean().unstack().reindex(index=order,columns=METHODS)
    ptable.to_csv(tables/'pressure_latency.csv',encoding='utf-8-sig')
    fig,ax=plt.subplots(figsize=(12,4),layout='constrained')
    image=ax.imshow(ptable,norm=LogNorm(),cmap='YlOrRd',aspect='auto')
    ax.set_xticks(np.arange(8),SHORT)
    ax.set_yticks(range(4),['1000船 × 1条/船窗','1000船 × 5条/船窗','10000船 × 1条/船窗','10000船 × 5条/船窗'])
    for row in range(4):
        for col in range(8):
            value=ptable.iloc[row,col]
            ax.text(col,row,f'{value:.1f}',ha='center',va='center',fontsize=9,color='white' if image.norm(value)>.6 else 'black')
    fig.colorbar(image,ax=ax,label='限制平均等待 / 秒（对数色阶）')
    ax.set_title('图5  合成压力场景：每方法5种子，每次6个评估窗口')
    save(fig,'fig05_pressure','位置、消息时间及连续合成MMSI均为模拟。源复制0.8、burst、WFQ；每格为5次运行平均。该图衡量系统负载，不提供真实船流外推证据。')
    faults=primary[primary.phase.eq('faults')]
    cases=['control_loss','stale_epoch','capture_bound_breach','missing_seal','capture_blind_spot']
    fault_rows=[{'case':c,'method':m,**stats(faults[faults.case_id.eq(c)&faults.method.eq(m)])} for c in cases for m in METHODS]
    fault_table=pd.DataFrame(fault_rows)
    fault_table.to_csv(tables/'fault_metrics.csv',index=False,encoding='utf-8-sig')
    fig,axes=plt.subplots(3,1,figsize=(12,12),layout='constrained')
    for ax,metric,title,cmap in zip(axes,['coverage','false_rate','invalidated_rate'],
                                  ['(a) 有效确认覆盖率 / %','(b) 错误确认率：曾声明窗作分母 / %','(c) 曾作废确认窗口：全部窗作分母 / %'],
                                  ['Blues','OrRd','Purples']):
        data=fault_table.pivot(index='case',columns='method',values=metric).reindex(index=cases,columns=METHODS)*100
        image=ax.imshow(np.ma.masked_invalid(data.to_numpy()),cmap=cmap,vmin=0,vmax=100 if metric=='coverage' else max(1,float(data.max().max())),aspect='auto')
        ax.set_xticks(range(8),SHORT); ax.set_yticks(range(5),['控制丢包20%','过期epoch','封闭界违约','缺失SEAL','所有源采集盲区'])
        for row in range(5):
            for col in range(8):
                v=data.iloc[row,col]
                label='无声明' if np.isnan(v) else (f'{v:.3f}' if metric=='false_rate' else f'{v:.1f}')
                ax.text(col,row,label,ha='center',va='center',fontsize=9,
                        color='white' if not np.isnan(v) and image.norm(v)>.6 else 'black')
        ax.set_title(title)
        fig.colorbar(image,ax=ax,shrink=.8,label='%')
    fig.suptitle('图6  故障边界：低错误率需要结合覆盖率与作废记录解释')
    save(fig,'fig06_faults','每方法每故障3种子，共8208个窗口实例；使用六项基线修复替代后的正式汇总。灰白无声明格不等于零风险。错误计数确认与后来作废证明分开统计，不能相加当不同窗口。采集盲区的真值是源采集并集，不是真实海域。')
    fig,axes=plt.subplots(1,2,figsize=(13,5.5),layout='constrained')
    for ax,data,title in [(axes[0],replay,'(a) 正常19日回放'),(axes[1],pressure[pressure.case_id.eq('n10000_r5')],'(b) 合成10000船 × 5条/船窗')]:
        for i,m in enumerate(METHODS):
            s=stats(data[data.method.eq(m)])
            ax.scatter(s['control']/1024,s['wait'],s=85,color=COLORS[i],marker=['o','s','^','D','v','P','X','*'][i],label=SHORT[i])
            # Slight annotation offsets only; the data point itself is never jittered.
            offsets=[(10,12),(10,0),(25,16),(-55,25),(12,-19),(-55,18),(22,-26),(22,8)]
            if ax is axes[1] and i>=4:
                positions={4:(.72,.28),5:(.87,.16),6:(.50,.18),7:(.45,.32)}
                ax.annotate(SHORT[i],(s['control']/1024,s['wait']),xytext=positions[i],textcoords='axes fraction',fontsize=8,
                            arrowprops={'arrowstyle':'-','color':COLORS[i],'linewidth':.7})
            else:
                if ax is axes[1] and i==3:
                    offsets[i]=(25,-25)
                ax.annotate(SHORT[i],(s['control']/1024,s['wait']),xytext=offsets[i],textcoords='offset points',fontsize=8,
                            arrowprops={'arrowstyle':'-','color':COLORS[i],'linewidth':.6})
        ax.set_xlabel('平均控制网络 / KiB·运行⁻¹'); ax.set_ylabel('限制平均等待 / 秒')
        ax.set_title(title); ax.grid(alpha=.2); ax.margins(.18)
        ax.set_ylim(bottom=0)
        ax.set_xlim(left=-max(1,data.control_bytes.mean()/1024)*.03)
    fig.suptitle('图7  延迟—控制通信取舍（须同时查看准确性与覆盖率）')
    save(fig,'fig07_tradeoff','显示全部八方法，不以二维投影宣布总体Pareto最优。DIFF-H与DIFF-E点接近或重合；WM/TIMER较低控制字节不提供精确证明。坐标零点保留；注释偏移不改变数据点。')
    fig,ax=plt.subplots(figsize=(9,4.5),layout='constrained')
    model_names=['M0','M1','M2']
    x=np.arange(2)
    for i,m in enumerate(model_names):
        values=[]
        for scene in ['iid','burst']:
            found=failure[failure.scenario.eq(scene)&failure.model.eq(m)]
            if len(found)!=1:
                raise ValueError(f'failed pilot metric missing: {scene}/{m}')
            values.append(float(found.brier.iloc[0]))
        ax.bar(x+(i-1)*.24,values,.22,color=[COLORS[0],COLORS[6],COLORS[3]][i],label=['M0：聚合历史基线','M1：增加身份历史','M2：画像置换对照'][i])
        for p,v in zip(x+(i-1)*.24,values):
            ax.text(p,v+.004,f'{v:.4f}',ha='center',fontsize=9)
    ax.set_xticks(x,['旧预实验：独立延迟','旧预实验：集中补传'])
    ax.set_ylabel('测试 Brier 损失（越低越好）'); ax.set_ylim(0,.36)
    ax.legend(fontsize=9); ax.set_title('图8  失败起点：身份历史没有稳定改善漏计概率预测')
    save(fig,'fig08_failed_prediction','来自原固定C模型、五种子平均测试Brier，不重新拟合或调参。它是848项协议实验之前的另一任务和模拟器，不能把Brier与本报告计数误差直接横向比较。M0本身已经含聚合历史，M2也不是完全无历史。')
    pair=pd.read_csv(summary/'paired_date_differences_vs_count.csv')
    fig,ax=plt.subplots(figsize=(12,4.5),layout='constrained')
    for i,m in enumerate(['SEALED_DIFF_EXACT','FULL_ID_MANIFEST','ID_STREAM_30S']):
        g=pair[pair.method.eq(m)].groupby('date').restricted_mean_wait_difference.mean()
        ax.plot(range(1,len(g)+1),g,marker=['o','s','^'][i],color=[COLORS[6],COLORS[4],COLORS[5]][i],label=m)
    ax.axhline(0,color='black',linewidth=.8)
    ax.set_xticks(range(1,20)); ax.set_xlabel('2022年1月原UTC日期'); ax.set_ylabel('相对COUNT的限制等待差 / 秒（负值更快）')
    ax.grid(alpha=.2); ax.legend(fontsize=9); ax.set_title('图9  日期配对结果：先平均模拟环境与种子，不扩充真实样本量')
    save(fig,'fig09_daily_paired','每个点是一个原UTC日期的配对差，平均该日期的复制率、链路、调度及种子重复；19日期此前已用于探索。这些点不是置信区间，也未宣称统计显著。')
    (output/'figure_captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf-8')
    provenance['figures']={p.name:sha(p) for p in sorted(figures.glob('*')) if p.is_file()}
    provenance['tables']={p.name:sha(p) for p in sorted(tables.glob('*.csv'))}
    (output/'figure_manifest.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'figures':len(captions),'formats':['png','pdf','svg'],'accuracy_rows':len(accuracy),'output':str(output)},ensure_ascii=False))

if __name__=='__main__':
    main()
