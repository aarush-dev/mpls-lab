define(["@emotion/css","@grafana/data","@grafana/runtime","@grafana/ui","react","react-router-dom"],(e,t,n,o,l,a)=>(()=>{"use strict";var r={89(t){t.exports=e},781(e){e.exports=t},531(e){e.exports=n},7(e){e.exports=o},959(e){e.exports=l},806(e){e.exports=a}};const c={};function i(e){const t=c[e];if(void 0!==t)return t.exports;const n=c[e]={exports:{}};return r[e](n,n.exports,i),n.exports}i.n=e=>{const t=e&&e.__esModule?()=>e.default:()=>e;return i.d(t,{a:t}),t},i.d=(e,t)=>{if(Array.isArray(t))for(var n=0;n<t.length;){var o=t[n++],l=t[n++];i.o(e,o)?0===l&&n++:0===l?Object.defineProperty(e,o,{enumerable:!0,value:t[n++]}):Object.defineProperty(e,o,{enumerable:!0,get:l})}else for(var o in t)i.o(t,o)&&!i.o(e,o)&&Object.defineProperty(e,o,{enumerable:!0,get:t[o]})},i.o=(e,t)=>Object.prototype.hasOwnProperty.call(e,t),i.r=e=>{Symbol.toStringTag&&Object.defineProperty(e,Symbol.toStringTag,{value:"Module"}),Object.defineProperty(e,"__esModule",{value:!0})};let u={};i.r(u),i.d(u,{plugin:()=>R});var s=i(781),m=i(959),p=i.n(m),g=i(806),d=i(89),E=i(7),f=i(531);function y(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Overview"),p().createElement("p",null,"Coming soon."))}function v(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Topology"),p().createElement("p",null,"Coming soon."))}function b(){const{id:e}=(0,g.useParams)();return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Node Detail",e?`: ${e}`:""),p().createElement("p",null,"Coming soon."))}function P(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Telemetry"),p().createElement("p",null,"Coming soon."))}function h(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Incidents"),p().createElement("p",null,"Coming soon."))}function x(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Copilot"),p().createElement("p",null,"Coming soon."))}function $(){return p().createElement(f.PluginPage,null,p().createElement("h1",null,"Status"),p().createElement("p",null,"Coming soon."))}const C=[{to:"",label:"Overview",exact:!0},{to:"topology",label:"Topology"},{to:"node/1",label:"Node Detail"},{to:"telemetry",label:"Telemetry"},{to:"incidents",label:"Incidents"},{to:"copilot",label:"Copilot"},{to:"status",label:"Status"}],O=e=>({root:d.css`
    display: flex;
    flex-direction: column;
  `,nav:d.css`
    display: flex;
    gap: ${e.spacing(2)};
    padding: ${e.spacing(1)} 0;
    border-bottom: 1px solid ${e.colors.border.weak};
    margin-bottom: ${e.spacing(2)};
  `,navLink:d.css`
    color: ${e.colors.text.secondary};
    text-decoration: none;
    padding: ${e.spacing(.5)} ${e.spacing(1)};
  `,navLinkActive:d.css`
    color: ${e.colors.text.primary};
    font-weight: ${e.typography.fontWeightMedium};
  `,content:d.css`
    flex: 1;
  `}),R=(new s.AppPlugin).setRootPage(function(e){const t=(0,E.useStyles2)(O),{path:n,url:o}=(0,g.useRouteMatch)();return p().createElement("div",{className:t.root},p().createElement("nav",{className:t.nav},C.map(e=>p().createElement(g.NavLink,{key:e.to,exact:e.exact,to:e.to?`${o}/${e.to}`:o,className:t.navLink,activeClassName:t.navLinkActive},e.label))),p().createElement("div",{className:t.content},p().createElement(g.Switch,null,p().createElement(g.Route,{exact:!0,path:n,component:y}),p().createElement(g.Route,{path:`${n}/topology`,component:v}),p().createElement(g.Route,{path:`${n}/node/:id`,component:b}),p().createElement(g.Route,{path:`${n}/telemetry`,component:P}),p().createElement(g.Route,{path:`${n}/incidents`,component:h}),p().createElement(g.Route,{path:`${n}/copilot`,component:x}),p().createElement(g.Route,{path:`${n}/status`,component:$}))))});return u})());
//# sourceMappingURL=module.js.map