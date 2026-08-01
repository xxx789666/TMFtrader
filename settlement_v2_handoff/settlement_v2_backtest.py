"""
settlement_v2 = settlement_v1 + 訊號強度濾網。
v1:每個結算週三都做(方向=sign(z(d_put_oi)+z(fx_dnet)))。
v2:只在 |z-sum| >= ZTHR 時才做,弱訊號空手。不設止損,週三開盤進、收盤出、1口。
"""
import numpy as np, pandas as pd

ZTHR=0.7; COST_PTS=4.0; PV_MXF,PV_TXF=50,200; WARMUP=40

df=pd.read_parquet('data/opt/settlement_probe.parquet')
df=df.dropna(subset=['d_put_oi','fx_dnet','r_day']).sort_values('wed').reset_index(drop=True)

fut=pd.read_parquet('data/vwap_fade/TXF_full_5m.parquet')
fut['date']=fut['datetime'].dt.normalize(); fut['t']=fut['datetime'].dt.time
day=fut[(fut['t']>=pd.Timestamp('08:45').time())&(fut['t']<=pd.Timestamp('13:45').time())]
daily=day.groupby('date').agg(w_open=('open','first'),w_close=('close','last'))
df=df.merge(daily,left_on='wed',right_index=True,how='left')

# expanding-z 連續強度
N=len(df); zsum=np.full(N,np.nan)
for i in range(N):
    if i<WARMUP: continue
    zsum[i]=sum((df[c].iloc[i]-df[c].iloc[:i].mean())/(df[c].iloc[:i].std()+1e-9) for c in ['d_put_oi','fx_dnet'])
df['zsum']=zsum
df=df.dropna(subset=['zsum','w_open','w_close']).copy()
df['sig']=np.sign(df.zsum)
df['do_take']=df.zsum.abs()>=ZTHR                   # v2 濾網
tr=df[df['do_take']].copy()
tr['side']=np.where(tr.sig>0,'long','short')
tr['pnl_pts']=tr.sig*(tr.w_close-tr.w_open)-COST_PTS
tr['pnl']=tr.pnl_pts*PV_MXF
tr['pnl_txf']=tr.pnl_pts*PV_TXF
tr['ret_pct']=(tr.sig*tr.r_day - COST_PTS/tr.w_open)*100
tr['year']=tr.wed.dt.year

out=tr[['wed','tue','zsum','d_put_oi','fx_dnet','sig','side','w_open','w_close','pnl_pts','pnl','ret_pct','year']].copy()
out.columns=['trade_date','signal_date','zsum','d_put_oi','fx_dnet','sig','side','entry','exit','pnl_pts','pnl','ret_pct','year']
out['note']=f'v2|z|>={ZTHR}、Wed開盤進收盤出、無止損、cost{COST_PTS}pt、MXF pv50'
out.to_csv('data/backtest_history/settlement_v2_2020_2025.csv',index=False)

def sharpe(x): return x.mean()/x.std()*np.sqrt(50)
cum=tr.pnl.cumsum(); dd=(cum-cum.cummax()).min()
print(f"=== settlement_v2 (|z-sum|>={ZTHR}, 無止損, cost {COST_PTS}pt) ===")
print(f"筆數 {len(tr)}  期間 {tr.wed.min().date()}~{tr.wed.max().date()}  (v1 是 269 筆,跳過 {269-len(tr)} 筆弱訊號)")
print(f"總淨利  MXF {tr.pnl.sum():,.0f}   TXF {tr.pnl_txf.sum():,.0f}")
print(f"平均 {tr.pnl.mean():+,.0f}/筆 ({tr.pnl_pts.mean():+.1f}pt)  勝率 {(tr.pnl>0).mean()*100:.0f}%")
pf=tr.pnl[tr.pnl>0].sum()/-tr.pnl[tr.pnl<0].sum()
print(f"PF {pf:.2f}   年化Sharpe {sharpe(tr.ret_pct/100):.2f}   最大單筆 +{tr.pnl.max():,.0f}/{tr.pnl.min():,.0f}   最大回撤 {dd:,.0f}")

print("\n=== 逐年 ===")
g=tr.groupby('year').agg(筆數=('pnl','size'),淨利MXF=('pnl','sum'),勝率=('pnl',lambda x:f'{(x>0).mean()*100:.0f}%'),PF=('pnl_pts',lambda x:round(x[x>0].sum()/(-x[x<0].sum()+1e-9),2)))
print(g.to_string())

print("\n=== 前6筆 / 後6筆 ===")
cols=['trade_date','zsum','side','entry','exit','pnl_pts','pnl']
print(out[cols].head(6).to_string(index=False)); print('...'); print(out[cols].tail(6).to_string(index=False))

# 相關性
tr['ym']=tr.wed.dt.to_period('M'); sv=tr.groupby('ym')['pnl'].sum()
def lm(p,d,c): x=pd.read_csv(p); x[d]=pd.to_datetime(x[d]); return x.groupby(x[d].dt.to_period('M'))[c].sum()
S={'settlement_v2':sv,'chips_combo':lm('data/backtest_history/chips_2020_2025.csv','trade_date','pnl'),
   'maxpain_v2':lm('data/backtest_history/maxpain_2020_2025.csv','exit_date','pnl'),
   'maxpain_v3':lm('data/backtest_history/maxpain_v3trail125_2020_2025.csv','entry_t1','pnl')}
M=pd.DataFrame(S).fillna(0.0)
print("\n=== 月報酬相關性 (vs 既有) ===")
print(M.corr()['settlement_v2'].drop('settlement_v2').round(3).to_string())
print("\nsaved data/backtest_history/settlement_v2_2020_2025.csv")
