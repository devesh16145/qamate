// Lucide-style line icons — built with React.createElement to avoid JSX parser quirks with long inline paths
const h = React.createElement;
const I = {};

const ICONS = {
  History: [
    ['path', { d: 'M3 11a9 9 0 1 1 2.6 6.4M3 4v7h7' }],
    ['path', { d: 'M12 7v5l3 2' }],
  ],
  Play: [['polygon', { points: '6 4 20 12 6 20 6 4', fill: 'currentColor', stroke: 'none' }]],
  ArrowUp: [
    ['line', { x1: 12, y1: 19, x2: 12, y2: 5 }],
    ['polyline', { points: '5 12 12 5 19 12' }],
  ],
  Paperclip: [['path', { d: 'M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5' }]],
  PenSquare: [
    ['path', { d: 'M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7' }],
    ['path', { d: 'M18.4 2.6a2.1 2.1 0 1 1 3 3L12 15l-4 1 1-4 9.4-9.4z' }],
  ],
  Sliders: [
    ['line', { x1: 4, y1: 21, x2: 4, y2: 14 }], ['line', { x1: 4, y1: 10, x2: 4, y2: 3 }],
    ['line', { x1: 12, y1: 21, x2: 12, y2: 12 }], ['line', { x1: 12, y1: 8, x2: 12, y2: 3 }],
    ['line', { x1: 20, y1: 21, x2: 20, y2: 16 }], ['line', { x1: 20, y1: 12, x2: 20, y2: 3 }],
    ['line', { x1: 1, y1: 14, x2: 7, y2: 14 }], ['line', { x1: 9, y1: 8, x2: 15, y2: 8 }], ['line', { x1: 17, y1: 16, x2: 23, y2: 16 }],
  ],
  Globe: [
    ['circle', { cx: 12, cy: 12, r: 10 }],
    ['line', { x1: 2, y1: 12, x2: 22, y2: 12 }],
    ['path', { d: 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z' }],
  ],
  Stop: [['rect', { x: 5, y: 5, width: 14, height: 14, rx: 2 }]],
  Record: [['circle', { cx: 12, cy: 12, r: 6, fill: 'currentColor', stroke: 'none' }]],
  Pause: [
    ['rect', { x: 6, y: 5, width: 4, height: 14, rx: 1, fill: 'currentColor', stroke: 'none' }],
    ['rect', { x: 14, y: 5, width: 4, height: 14, rx: 1, fill: 'currentColor', stroke: 'none' }],
  ],
  User: [
    ['circle', { cx: 12, cy: 8, r: 4 }],
    ['path', { d: 'M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8' }],
  ],
  Shield: [['path', { d: 'M12 3l8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6l8-3z' }]],
  Zap: [['polygon', { points: '13 2 3 14 12 14 11 22 21 10 12 10 13 2' }]],
  Settings: [
    ['circle', { cx: 12, cy: 12, r: 3 }],
    ['path', { d: 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' }],
  ],
  Search: [
    ['circle', { cx: 11, cy: 11, r: 7 }],
    ['line', { x1: 21, y1: 21, x2: 16.65, y2: 16.65 }],
  ],
  X: [
    ['line', { x1: 18, y1: 6, x2: 6, y2: 18 }],
    ['line', { x1: 6, y1: 6, x2: 18, y2: 18 }],
  ],
  Check: [['polyline', { points: '20 6 9 17 4 12' }]],
  ChevronDown: [['polyline', { points: '6 9 12 15 18 9' }]],
  ChevronRight: [['polyline', { points: '9 6 15 12 9 18' }]],
  ChevronLeft: [['polyline', { points: '15 6 9 12 15 18' }]],
  MoreHorizontal: [
    ['circle', { cx: 12, cy: 12, r: 1.4, fill: 'currentColor' }],
    ['circle', { cx: 19, cy: 12, r: 1.4, fill: 'currentColor' }],
    ['circle', { cx: 5, cy: 12, r: 1.4, fill: 'currentColor' }],
  ],
  Folder: [['path', { d: 'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z' }]],
  FolderOpen: [['path', { d: 'M6 14l1.5-3a2 2 0 0 1 1.8-1.1H22l-3 7.5A2 2 0 0 1 17.1 19H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v2' }]],
  File: [
    ['path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }],
    ['polyline', { points: '14 2 14 8 20 8' }],
  ],
  FileText: [
    ['path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }],
    ['polyline', { points: '14 2 14 8 20 8' }],
    ['line', { x1: 16, y1: 13, x2: 8, y2: 13 }],
    ['line', { x1: 16, y1: 17, x2: 8, y2: 17 }],
    ['line', { x1: 10, y1: 9, x2: 8, y2: 9 }],
  ],
  Save: [
    ['path', { d: 'M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z' }],
    ['polyline', { points: '17 21 17 13 7 13 7 21' }],
    ['polyline', { points: '7 3 7 8 15 8' }],
  ],
  Download: [
    ['path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }],
    ['polyline', { points: '7 10 12 15 17 10' }],
    ['line', { x1: 12, y1: 15, x2: 12, y2: 3 }],
  ],
  Upload: [
    ['path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }],
    ['polyline', { points: '17 8 12 3 7 8' }],
    ['line', { x1: 12, y1: 3, x2: 12, y2: 15 }],
  ],
  Plus: [
    ['line', { x1: 12, y1: 5, x2: 12, y2: 19 }],
    ['line', { x1: 5, y1: 12, x2: 19, y2: 12 }],
  ],
  Trash: [
    ['polyline', { points: '3 6 5 6 21 6' }],
    ['path', { d: 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6' }],
    ['line', { x1: 10, y1: 11, x2: 10, y2: 17 }],
    ['line', { x1: 14, y1: 11, x2: 14, y2: 17 }],
  ],
  Copy: [
    ['rect', { x: 9, y: 9, width: 13, height: 13, rx: 2 }],
    ['path', { d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1' }],
  ],
  Pin: [
    ['line', { x1: 12, y1: 17, x2: 12, y2: 22 }],
    ['path', { d: 'M5 17h14v-1.76a2 2 0 0 0-1.11-1.79L15 12V7l1-1V3H8v3l1 1v5l-2.89 1.45A2 2 0 0 0 5 15.24z' }],
  ],
  Star: [['polygon', { points: '12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2' }]],
  AlertTriangle: [
    ['path', { d: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z' }],
    ['line', { x1: 12, y1: 9, x2: 12, y2: 13 }],
    ['line', { x1: 12, y1: 17, x2: 12.01, y2: 17 }],
  ],
  AlertCircle: [
    ['circle', { cx: 12, cy: 12, r: 10 }],
    ['line', { x1: 12, y1: 8, x2: 12, y2: 12 }],
    ['line', { x1: 12, y1: 16, x2: 12.01, y2: 16 }],
  ],
  CheckCircle: [
    ['path', { d: 'M22 11.08V12a10 10 0 1 1-5.93-9.14' }],
    ['polyline', { points: '22 4 12 14.01 9 11.01' }],
  ],
  Clock: [
    ['circle', { cx: 12, cy: 12, r: 10 }],
    ['polyline', { points: '12 6 12 12 16 14' }],
  ],
  Activity: [['polyline', { points: '22 12 18 12 15 21 9 3 6 12 2 12' }]],
  BarChart: [
    ['line', { x1: 12, y1: 20, x2: 12, y2: 10 }],
    ['line', { x1: 18, y1: 20, x2: 18, y2: 4 }],
    ['line', { x1: 6, y1: 20, x2: 6, y2: 16 }],
  ],
  Camera: [
    ['path', { d: 'M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z' }],
    ['circle', { cx: 12, cy: 13, r: 4 }],
  ],
  Video: [
    ['polygon', { points: '23 7 16 12 23 17 23 7' }],
    ['rect', { x: 1, y: 5, width: 15, height: 14, rx: 2, ry: 2 }],
  ],
  PanelLeft: [
    ['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }],
    ['line', { x1: 9, y1: 3, x2: 9, y2: 21 }],
  ],
  PanelRight: [
    ['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }],
    ['line', { x1: 15, y1: 3, x2: 15, y2: 21 }],
  ],
  PanelBottom: [
    ['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }],
    ['line', { x1: 3, y1: 15, x2: 21, y2: 15 }],
  ],
  Layout: [
    ['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }],
    ['line', { x1: 3, y1: 9, x2: 21, y2: 9 }],
    ['line', { x1: 9, y1: 21, x2: 9, y2: 9 }],
  ],
  Moon: [['path', { d: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z' }]],
  Sun: [
    ['circle', { cx: 12, cy: 12, r: 4 }],
    ['line', { x1: 12, y1: 2, x2: 12, y2: 4 }],
    ['line', { x1: 12, y1: 20, x2: 12, y2: 22 }],
    ['line', { x1: 4.93, y1: 4.93, x2: 6.34, y2: 6.34 }],
    ['line', { x1: 17.66, y1: 17.66, x2: 19.07, y2: 19.07 }],
    ['line', { x1: 2, y1: 12, x2: 4, y2: 12 }],
    ['line', { x1: 20, y1: 12, x2: 22, y2: 12 }],
    ['line', { x1: 4.93, y1: 19.07, x2: 6.34, y2: 17.66 }],
    ['line', { x1: 17.66, y1: 6.34, x2: 19.07, y2: 4.93 }],
  ],
  Keyboard: [
    ['rect', { x: 2, y: 6, width: 20, height: 12, rx: 2 }],
    ['line', { x1: 6, y1: 10, x2: 6, y2: 10 }],
    ['line', { x1: 10, y1: 10, x2: 10, y2: 10 }],
    ['line', { x1: 14, y1: 10, x2: 14, y2: 10 }],
    ['line', { x1: 18, y1: 10, x2: 18, y2: 10 }],
    ['line', { x1: 8, y1: 14, x2: 16, y2: 14 }],
  ],
  Filter: [['polygon', { points: '22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3' }]],
  Tag: [
    ['path', { d: 'M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z' }],
    ['line', { x1: 7, y1: 7, x2: 7.01, y2: 7 }],
  ],
  Bug: [
    ['rect', { x: 8, y: 6, width: 8, height: 14, rx: 4 }],
    ['path', { d: 'M16 13h5M3 13h5M16 9l3-2M5 7l3 2M16 17l3 2M5 19l3-2M12 2v4' }],
  ],
  GitMerge: [
    ['circle', { cx: 18, cy: 18, r: 3 }],
    ['circle', { cx: 6, cy: 6, r: 3 }],
    ['path', { d: 'M6 21V9a9 9 0 0 0 9 9' }],
  ],
  RefreshCw: [
    ['polyline', { points: '23 4 23 10 17 10' }],
    ['polyline', { points: '1 20 1 14 7 14' }],
    ['path', { d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15' }],
  ],
  ExternalLink: [
    ['path', { d: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6' }],
    ['polyline', { points: '15 3 21 3 21 9' }],
    ['line', { x1: 10, y1: 14, x2: 21, y2: 3 }],
  ],
  Calendar: [
    ['rect', { x: 3, y: 4, width: 18, height: 18, rx: 2 }],
    ['line', { x1: 16, y1: 2, x2: 16, y2: 6 }],
    ['line', { x1: 8, y1: 2, x2: 8, y2: 6 }],
    ['line', { x1: 3, y1: 10, x2: 21, y2: 10 }],
  ],
  MessageSquare: [['path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' }]],
  TrendingUp: [
    ['polyline', { points: '23 6 13.5 15.5 8.5 10.5 1 18' }],
    ['polyline', { points: '17 6 23 6 23 12' }],
  ],
  TrendingDown: [
    ['polyline', { points: '23 18 13.5 8.5 8.5 13.5 1 6' }],
    ['polyline', { points: '17 18 23 18 23 12' }],
  ],
  Eye: [
    ['path', { d: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z' }],
    ['circle', { cx: 12, cy: 12, r: 3 }],
  ],
  EyeOff: [
    ['path', { d: 'M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24' }],
    ['line', { x1: 1, y1: 1, x2: 23, y2: 23 }],
  ],
  Beaker: [['path', { d: 'M4.5 3h15M6 3v7L3 17a2 2 0 0 0 1.8 3h14.4a2 2 0 0 0 1.8-3L18 10V3M9 14h6' }]],
  Box: [
    ['path', { d: 'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z' }],
    ['polyline', { points: '3.27 6.96 12 12.01 20.73 6.96' }],
    ['line', { x1: 12, y1: 22.08, x2: 12, y2: 12 }],
  ],
  Menu: [
    ['line', { x1: 4, y1: 7, x2: 20, y2: 7 }],
    ['line', { x1: 4, y1: 12, x2: 20, y2: 12 }],
    ['line', { x1: 4, y1: 17, x2: 20, y2: 17 }],
  ],
  Sparkle: [
    ['path', { d: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z' }],
    ['path', { d: 'M19 17l.7 1.8L21.5 19.5l-1.8.7L19 22l-.7-1.8L16.5 19.5l1.8-.7L19 17z' }],
  ],
  Layers: [
    ['polygon', { points: '12 2 2 7 12 12 22 7 12 2' }],
    ['polyline', { points: '2 17 12 22 22 17' }],
    ['polyline', { points: '2 12 12 17 22 12' }],
  ],
  ListChecks: [
    ['polyline', { points: '3 8 5 10 9 6' }],
    ['polyline', { points: '3 16 5 18 9 14' }],
    ['line', { x1: 12, y1: 8, x2: 21, y2: 8 }],
    ['line', { x1: 12, y1: 16, x2: 21, y2: 16 }],
  ],
  Edit: [
    ['path', { d: 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7' }],
    ['path', { d: 'M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z' }],
  ],
  Table: [
    ['path', { d: 'M12 3v18' }],
    ['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }],
    ['path', { d: 'M3 9h18' }],
    ['path', { d: 'M3 15h18' }],
  ],
  Braces: [
    ['path', { d: 'M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1' }],
    ['path', { d: 'M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1' }],
  ],
  Database: [
    ['ellipse', { cx: 12, cy: 5, rx: 9, ry: 3 }],
    ['path', { d: 'M3 5v14a9 3 0 0 0 18 0V5' }],
    ['path', { d: 'M3 12a9 3 0 0 0 18 0' }],
  ],
};

Object.keys(ICONS).forEach((name) => {
  const parts = ICONS[name];
  I[name] = ({ size = 16, stroke = 1.75, ...props }) =>
    h(
      'svg',
      {
        width: size,
        height: size,
        viewBox: '0 0 24 24',
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth: stroke,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        ...props,
      },
      parts.map(([tag, attrs], i) => h(tag, { key: i, ...attrs }))
    );
});

window.I = I;
