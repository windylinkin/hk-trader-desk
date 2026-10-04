let quantChart=null,quantCandles=null,equityChart=null,equityLine=null;
function applyTheme(value){
 document.documentElement.dataset.theme=value;localStorage.setItem('deskTheme',value);
 const light=value==='light',options={layout:{background:{color:light?'#ffffff':'#121a23'},textColor:light?'#475569':'#8b9bab'},grid:{vertLines:{color:light?'#eef2f6':'#1d2834'},horzLines:{color:light?'#eef2f6':'#1d2834'}}};
 for(const c of [chart,macdChart,quantChart,equityChart])if(c)c.applyOptions(options);
 $('themeToggle').textContent=light?'深色主题':'白色主题';
}
async function runQuant(){
 const body={market:$('quantMarket').value,symbol:$('quantSymbol').value.trim(),exchange:$('quantExchange').value,period:$('quantPeriod').value,end:$('quantEnd').value,fast:Number($('quantFast').value),slow:Number($('quantSlow').value),capital:Number($('quantCapital').value),allocation:Number($('quantAllocation').value),fee_bps:Number($('quantFee').value),slippage_bps:Number($('quantSlippage').value),lot_size:Number($('quantLot').value)};
 $('quantMessage').textContent='正在获取行情并计算…';
 try{
 const data=await api('/api/quant/backtest',body),s=data.summary;
 $('quantMessage').textContent=data.method;
 $('quantSummary').innerHTML=[['期末权益',fmt(s.final_equity)],['区间收益',pct(s.return_pct)],['最大回撤',pct(s.max_drawdown_pct)],['已平仓次数',s.closed_trades],['已平仓盈利占比',s.win_rate_pct==null?'—':pct(s.win_rate_pct)],['期末持仓',fmt(s.quantity,8)]].map(([k,v])=>`<div><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`).join('');
 if(!quantChart){quantChart=LightweightCharts.createChart($('quantChart'),{...chartOptions,width:$('quantChart').clientWidth,height:350});quantCandles=quantChart.addCandlestickSeries({upColor:'#16a085',downColor:'#e65c6b',borderVisible:false,wickUpColor:'#16a085',wickDownColor:'#e65c6b'});equityChart=LightweightCharts.createChart($('equityChart'),{...chartOptions,width:$('equityChart').clientWidth,height:200});equityLine=equityChart.addLineSeries({color:'#2586d8',priceFormat:{type:'price',precision:2,minMove:.01}});}
 quantCandles.setData(data.bars);quantCandles.setMarkers(data.trades.map(t=>({time:t.time,position:t.side==='buy'?'belowBar':'aboveBar',color:t.side==='buy'?'#16a085':'#e65c6b',shape:t.side==='buy'?'arrowUp':'arrowDown',text:t.side==='buy'?'模拟买入':'模拟卖出'})));equityLine.setData(data.equity);quantChart.timeScale().fitContent();equityChart.timeScale().fitContent();applyTheme(document.documentElement.dataset.theme||'dark');
 $('quantTrades').innerHTML=data.trades.slice(-100).reverse().map(t=>`<tr><td>${esc(new Date(t.time*1000).toISOString().replace('T',' ').slice(0,19))}</td><td>${t.side==='buy'?'买入':'卖出'}</td><td>${fmt(t.price,3)}</td><td>${fmt(t.quantity,8)}</td><td>${fmt(t.fee)}</td><td>${t.pnl==null?'—':fmt(t.pnl)}</td></tr>`).join('')||'<tr><td colspan="6">当前参数和历史区间未发生交叉成交</td></tr>';
 }catch(e){$('quantMessage').textContent=e.message;throw e;}
}
function quantParameters(){return {market:$('quantMarket').value,symbol:$('quantSymbol').value.trim(),exchange:$('quantExchange').value,period:$('quantPeriod').value,end:$('quantEnd').value,fast:Number($('quantFast').value),slow:Number($('quantSlow').value),capital:Number($('quantCapital').value),allocation:Number($('quantAllocation').value),fee_bps:Number($('quantFee').value),slippage_bps:Number($('quantSlippage').value),lot_size:Number($('quantLot').value)}}
async function loadPaper(){const s=await api('/api/quant/paper');$('paperStatus').textContent=s.status+(s.equity!=null?` · 模拟权益 ${fmt(s.equity)} · 持仓 ${fmt(s.quantity,8)} · 已记录 ${s.trades.length} 笔成交`:'');}
action('startPaper',async()=>{await api('/api/quant/paper',quantParameters());await loadPaper();toast('新的持续模拟账户已建立，等待已收盘行情')});
action('pausePaper',async()=>{await api('/api/quant/paper/pause',{});await loadPaper()});
loadPaper().catch(()=>{});setInterval(()=>loadPaper().catch(()=>{}),15000);
action('runQuant',runQuant);
$('quantMarket').onchange=()=>{$('quantSymbol').value=$('quantMarket').value==='hk'?'HK.00700':$('quantExchange').value==='kraken'?'BTC/USD':'BTC/USDT';$('quantLot').disabled=$('quantMarket').value==='btc';$('quantExchange').disabled=$('quantMarket').value==='hk'};
$('quantExchange').onchange=()=>{if($('quantMarket').value==='btc')$('quantSymbol').value=$('quantExchange').value==='kraken'?'BTC/USD':'BTC/USDT'};
$('themeToggle').onclick=()=>applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light');
applyTheme(localStorage.getItem('deskTheme')||'dark');
api('/api/quant/config').then(p=>{for(const [id,key] of Object.entries({quantMarket:'market',quantSymbol:'symbol',quantExchange:'exchange',quantPeriod:'period',quantEnd:'end',quantFast:'fast',quantSlow:'slow',quantCapital:'capital',quantAllocation:'allocation',quantFee:'fee_bps',quantSlippage:'slippage_bps',quantLot:'lot_size'}))$(id).value=p[key];$('quantLot').disabled=p.market==='btc';$('quantExchange').disabled=p.market==='hk'}).catch(()=>{});
new ResizeObserver(()=>{if(quantChart)quantChart.resize($('quantChart').clientWidth,350);if(equityChart)equityChart.resize($('equityChart').clientWidth,200)}).observe($('quantChart'));
