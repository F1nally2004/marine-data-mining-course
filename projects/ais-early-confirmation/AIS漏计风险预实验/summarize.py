"""Describe pilot results, including an intercept-only reference; never retunes models."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'outputs'


def run():
    metrics = pd.read_csv(OUT/'metrics.csv')
    counts = pd.read_csv(OUT/'label_counts.csv')
    predictions = pd.read_csv(OUT/'test_predictions.csv')
    audit = pd.read_csv(OUT/'simulation_audit.csv')
    prior_rows, calibration, empty_rows = [],[],[]
    for (scenario,seed), p in predictions.groupby(['scenario','seed']):
        train = counts.loc[(counts.scenario == scenario) & (counts.seed == seed) & (counts.split == 'train')].iloc[0]
        prior = train.positive_windows/train.windows
        prior_rows.append({'scenario':scenario,'seed':int(seed),'training_prior':prior,
                           'brier':float(np.mean((p.y-prior)**2))})
        labels = pd.read_csv(OUT/f'labels_{scenario}_{seed}.csv')
        p = p.merge(labels[['window_start','truth_ships']],on='window_start',validate='one_to_one')
        for method in ['M0','M1','M2']:
            for bin_id,g in p.groupby(pd.cut(p[method],np.linspace(0,1,11),include_lowest=True),observed=True):
                calibration.append({'scenario':scenario,'seed':seed,'method':method,'bin':str(bin_id),
                                    'n':len(g),'mean_probability':g[method].mean(),'observed_rate':g.y.mean()})
            for empty,g in p.groupby(p.truth_ships == 0):
                empty_rows.append({'scenario':scenario,'seed':seed,'method':method,'truth_empty':bool(empty),
                                   'n':len(g),'mean_probability':g[method].mean(),'observed_rate':g.y.mean(),
                                   'brier':float(np.mean((g.y-g[method])**2))})
    priors = pd.DataFrame(prior_rows)
    priors.to_csv(OUT/'constant_prior_reference.csv',index=False)
    pd.DataFrame(calibration).to_csv(OUT/'calibration.csv',index=False)
    pd.DataFrame(empty_rows).to_csv(OUT/'empty_window_diagnostic.csv',index=False)
    data = json.loads((ROOT/'data/data_summary.json').read_text())
    manifest = json.loads((OUT/'run_manifest.json').read_text())
    lines = ['# AIS 漏计风险预实验：实际运行结果','',
             '本报告由 summarize.py 从输出表生成；未根据测试结果修改模拟参数或模型特征。','',
             f"原始教程样本：{data['rows']:,} 条、{data['ships']} 个 MMSI；分析使用前 19 个完整日历日的 {manifest['source_rows_used']:,} 条、{manifest['ships']} 个 MMSI。",
             '数据是教程选取子集，未确认完整区域覆盖；原始数据库 source 字段为 Testing。模拟交付时间不是实际通信延迟。','',
             '## 1. 模拟及标签校验','',
             '所有窗口均用直接集合差独立复核；sanity 场景必须全部零漏计。事件时间早于 W=max_seen−180s 是诊断性迟到，固定首发截止后的报文另行统计；本实验不按水位线丢弃。','',
             '|情景|乱序率均值|落后水位线比例均值|晚于固定首发比例均值|进入补传队列比例均值|',
             '|---|---:|---:|---:|---:|']
    for scenario,g in audit.groupby('scenario'):
        vals = [g[c].mean()*100 for c in ['out_of_order_rate','behind_watermark_rate','after_publish_rate','queued_rate']]
        lines.append(f"|{scenario}|"+'|'.join(f'{v:.2f}%' for v in vals)+'|')
    lines += ['', '## 2. 测试期标签数量','',
              '|情景|每个种子测试窗口|阳性窗口范围|阳性率范围|','|---|---:|---:|---:|']
    for scenario,g in counts.query("split == 'test'").groupby('scenario'):
        lines.append(f'|{scenario}|{int(g.windows.iloc[0])}|{int(g.positive_windows.min())}–{int(g.positive_windows.max())}|{g.positive_rate.min():.2%}–{g.positive_rate.max():.2%}|')
    lines += ['', '## 3. 固定时刻概率预测','',
              '主指标为 Brier loss，越小越好。表中为五个模拟种子的均值；这些种子共享同一份真实记录，不是五份独立海上交通样本。', '',
              '|情景|训练期常数概率参考|M0 基线|M1 真实历史|M2 置换历史|M0−M1|',
              '|---|---:|---:|---:|---:|---:|']
    for scenario,g in metrics.groupby('scenario'):
        avg = g.groupby('method').brier.mean()
        prior = priors.loc[priors.scenario == scenario,'brier'].mean()
        lines.append(f"|{scenario}|{prior:.6f}|{avg['M0']:.6f}|{avg['M1']:.6f}|{avg['M2']:.6f}|{avg['M0']-avg['M1']:+.6f}|")
    lines += ['', '## 4. 概率均值检查','',
              '|情景|实际漏计发生率|M0 平均预测|M1 平均预测|M2 平均预测|',
              '|---|---:|---:|---:|---:|']
    for scenario,g in predictions.groupby('scenario'):
        lines.append(f"|{scenario}|{g.y.mean():.2%}|{g.M0.mean():.2%}|{g.M1.mean():.2%}|{g.M2.mean():.2%}|")
    shift = pd.read_csv(OUT/'features_burst_11.csv').groupby('split').agg(
        windows=('y','size'), empty_windows=('truth_ships',lambda x:int((x==0).sum())),
        maximum_source_lag_s=('source_time_lag','max'))
    shift.to_csv(OUT/'distribution_shift_diagnostic.csv')
    lines += ['', '集中补传 seed=11 的分布诊断：训练 864 个窗口中空记录窗口为 0，测试 576 个窗口中为空的有 117 个。训练最大 source_time_lag 为约 1,102 秒，测试为 70,652 秒。集中补传模型在这些空记录窗口几乎全部预测高风险，是总体 Brier 恶化的重要组成。',
              '这说明存在明显的输入范围变化；不等于已经证明单一特征是全部失败原因。空记录仅表示教程子集没有记录，不能解释为现实海区没有船。']
    lines += ['', '## 5. 能支持什么结论','',
              '- 技术流程通过：生成了乱序、补传以及非零 distinct 漏计事件，集合差标签与最早到达法一致。',
              '- 当前小样本和预定特征没有显示真实历史组的稳定 Brier 改善；不得据此宣称历史信息有效。',
              '- 集中补传下三组概率模型均明显失准，且差于训练期常数概率参考；在加强基线、增加独立日期前，不宜解释为历史信息无效。',
              '- 原数据只是 54 个 MMSI 的教程子集；测试只有四天，独立迟到每个种子只有二十余个阳性窗口。足够调试，不足以支撑普遍结论或可靠的显著性检验。',
              '- 五个种子结果、按日配对差、概率分箱及空窗口诊断全部保留。可据此提出新实验，但不得在当前测试集反复调参后继续称其为独立测试。',
              '', '## 6. 数据来源与复现','',
              '[Spadon, G. (2026). AISdb Tutorials, MAPS Lab, Dalhousie University](https://github.com/MAPS-Lab/AISdb-Tutorials)。原项目注明 AGPL-3.0；许可证和引用文件已随样本保存。',
              '',f"固定提交：`{json.loads((ROOT/'data/source_manifest.json').read_text())['commit']}`。",'']
    (ROOT/'预实验结果.md').write_text('\n'.join(lines),encoding='utf-8')
    files = list(OUT.glob('*.csv'))+list(OUT.glob('*.json'))+list(ROOT.glob('*.py'))+[ROOT/'requirements.txt']
    (OUT/'file_hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                                   for p in files if p.name != 'file_hashes.json'},indent=2),encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    run()
