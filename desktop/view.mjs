import { host, Popover, PopoverTrigger, PopoverContent } from '@hermes/plugin-sdk';
import { useEffect, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

function RouteIndicator({ctx}) {
  const [value,setValue]=useState({label:'Jev: checking',detail:'Reading routing support.'});
  const [compact,setCompact]=useState(() => ctx.storage.get('compact',false)===true);
  const [controller,setController]=useState(null);
  const [prompt,setPrompt]=useState('');
  const [starting,setStarting]=useState(false);
  const [actionStatus,setActionStatus]=useState('');
  useEffect(() => {
    const active=createRouteController(host,setValue,{storage:ctx.storage});
    setController(active);
    // No repair or restart. Poll only while the app is visible, at background priority.
    const cancel=ctx.setInterval(() => {if (document.visibilityState==='visible') active.refresh();},15000);
    const off=ctx.onEvent('session.info',event => active.observe(event));
    return () => {cancel(); off(); active.dispose();};
  },[ctx]);
  return jsxs(Popover,{children:[
    jsx(PopoverTrigger,{asChild:true,children:jsx('button',{
      type:'button', 'aria-label':value.label, title:value.label,
      style:{WebkitAppRegion:'no-drag',fontSize:12,maxWidth:360,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',padding:'4px 8px',borderRadius:6},
      children:compact && !value.label.includes('unavailable') && !value.label.includes('unsupported') ? 'Jev' : value.label
    })}),
    jsxs(PopoverContent,{align:'end',style:{fontSize:12,width:340},children:[
      jsx('div',{style:{fontWeight:600,marginBottom:8},children:value.label}),
      jsx('p',{children:value.detail}),
      jsxs('form',{onSubmit:async event => {
        event.preventDefault();
        if(starting) return;
        setStarting(true);setActionStatus('Choosing a route before Hermes creates the session.');
        try {
          const result=await createRoutedStart(host,ctx,prompt);
          setPrompt('');setActionStatus(result.submission==='unknown'
            ? 'The routed session exists, but prompt acceptance is unknown. Inspect that session before sending again.'
            : result.opened ? 'Routed session created and prompt submitted.'
              : 'Routed session created and prompt submitted. Select it from the sidebar to open it.');controller?.refresh();
        } catch {
          setActionStatus('Routed start failed. Check the Hermes gateway log for the rejected step.');
        } finally {setStarting(false);}
      },style:{display:'grid',gap:8,marginTop:12},children:[
        jsx('label',{htmlFor:'jev-routed-first-prompt',style:{fontWeight:600},children:'Start routed chat'}),
        jsx('textarea',{id:'jev-routed-first-prompt',value:prompt,maxLength:12000,rows:4,disabled:starting,
          onChange:event=>setPrompt(event.target.value),placeholder:'First prompt',
          style:{width:'100%',resize:'vertical',font:'inherit',padding:8,borderRadius:6}}),
        jsx('button',{type:'submit',disabled:starting || !prompt.trim(),children:starting ? 'Starting…' : 'Start routed chat'}),
        jsx('p',{style:{margin:0,opacity:.78},children:'This action works on compatible stock Hermes. The regular composer routes automatically only when Hermes provides the optional native turn-route hook.'}),
        actionStatus ? jsx('p',{role:'status',style:{margin:0},children:actionStatus}) : null
      ]}),
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
