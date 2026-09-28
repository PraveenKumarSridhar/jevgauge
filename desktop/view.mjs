import { host, Popover, PopoverTrigger, PopoverContent } from '@hermes/plugin-sdk';
import { useEffect, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

function RouteIndicator({ctx}) {
  const [value,setValue]=useState({label:'Jev: checking',detail:'Reading routing support.'});
  const [compact,setCompact]=useState(() => ctx.storage.get('compact',false)===true);
  const [controller,setController]=useState(null);
  useEffect(() => {
    const active=createRouteController(host,setValue);
    setController(active);
    // No repair or restart. Poll only while the app is visible, at background priority.
    const cancel=ctx.setInterval(() => {if (document.visibilityState==='visible') active.refresh();},15000);
    const off=ctx.onEvent('session.info',() => active.refresh());
    return () => {cancel(); off(); active.dispose();};
  },[ctx]);
  return jsxs(Popover,{children:[
    jsx(PopoverTrigger,{asChild:true,children:jsx('button',{
      type:'button', 'aria-label':value.label, title:value.label,
      style:{WebkitAppRegion:'no-drag',fontSize:12,maxWidth:360,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',padding:'4px 8px',borderRadius:6},
      children:compact && !value.label.includes('unavailable') && !value.label.includes('unsupported') ? 'Jev' : value.label
    })}),
    jsxs(PopoverContent,{align:'end',style:{fontSize:12,width:300},children:[
      jsx('div',{style:{fontWeight:600,marginBottom:8},children:value.label}),
      jsx('p',{children:value.detail}),
      jsxs('div',{style:{display:'flex',gap:12,marginTop:12},children:[
        jsx('button',{type:'button',onClick:() => {const next=!compact;setCompact(next);ctx.storage.set('compact',next);},children:compact ? 'Show model in title bar' : 'Minimize label'}),
        jsx('button',{type:'button',onClick:() => controller?.refresh(),children:'Refresh'})
      ]})
    ]})
  ]});
}

export default {
  id:'jev-router',name:'Jev routing',defaultEnabled:false,
  register(ctx) {
    ctx.register({id:'route-indicator',area:'titleBar.right',order:40,
      render:() => jsx(RouteIndicator,{ctx})});
  }
};
