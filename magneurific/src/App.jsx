import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import { useStore } from './store';
import { 
  MousePointer2, Settings, Share2, Focus, BoxSelect, Maximize, 
  Layers, PenTool, PaintBucket, Upload, Eraser, CircleDashed, MapPin,
  Sidebar as SidebarIcon, MessageSquare, ChevronRight, Hash, Sparkles,
  MessageSquarePlus, Pointer, Trash2, X, ZoomIn, ZoomOut, SlidersHorizontal, ChevronDown, Undo2, Box, Wand2, Save
} from 'lucide-react';

function hexToRgb(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? [ parseInt(result[1], 16), parseInt(result[2], 16), parseInt(result[3], 16) ] : [139, 92, 246];
}
import { motion } from 'framer-motion';

function DatasetSettings({ onClose }) {
  const { filters, setFilters } = useStore();
  
  return (
    <div className="settings-popover">
       <div className="settings-header">
           Dataset Settings
           <X size={16} cursor="pointer" onClick={onClose} />
       </div>
       <div className="setting-row">
           <span className="setting-label">Brightness ({filters.brightness}%)</span>
           <input type="range" min="0" max="200" value={filters.brightness} onChange={(e) => setFilters({brightness: e.target.value})} />
       </div>
       <div className="setting-row">
           <span className="setting-label">Contrast ({filters.contrast}%)</span>
           <input type="range" min="0" max="200" value={filters.contrast} onChange={(e) => setFilters({contrast: e.target.value})} />
       </div>
       <div className="setting-row" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '16px' }}>
           <input type="checkbox" id="invertCheck" checked={filters.invert} onChange={(e) => setFilters({invert: e.target.checked})} />
           <label htmlFor="invertCheck" style={{ fontSize: '13px', color: 'white' }}>Invert Colormap</label>
       </div>
    </div>
  );
}

function CanvasPopup({ ann, plane, zoom }) {
    const { removeAnnotation, removeComment, addComment, setActivePopup } = useStore();
    const [draft, setDraft] = useState('');

    const invScale = `scale(${1/zoom}) `;
    const invRotate = plane === 'YZ' ? 'rotate(-90deg)' : plane === 'XZ' ? 'rotate(90deg)' : '';

    return (
        <div 
           className="popup-anchor"
           style={{
               position: 'absolute',
               left: ann.x, top: ann.y,
               transform: invScale + invRotate,
               zIndex: 50,
               pointerEvents: 'none'
           }}
           onMouseDown={(e) => e.stopPropagation()}
           onDoubleClick={(e) => e.stopPropagation()}
        >
            <div className="canvas-popup" style={{ pointerEvents: 'auto' }}>
                <div className="canvas-popup-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                       <span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: ann.color }} />
                       {ann.label}
                       {ann.timestamp && <span style={{ marginLeft: 'auto', fontSize: '10px', color: 'var(--text-muted)', fontWeight: 'normal' }}>{ann.timestamp}</span>}
                    </div>
                    <X size={14} style={{ cursor: 'pointer', marginLeft: '6px' }} onClick={() => setActivePopup(null)} />
                </div>
                
                {ann.comments && ann.comments.length > 0 && (
                    <div className="comment-list" style={{ maxHeight: '150px', overflowY: 'auto' }}>
                        {ann.comments.map(c => (
                            <div key={c.id} className="comment-bubble" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '10px', color: 'var(--text-muted)' }}>
                                    <span style={{ fontWeight: 600 }}>{c.author}</span>
                                    <span>{c.timestamp}</span>
                                </div>
                                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                                    <span className="comment-text" style={{ padding: 0 }}>{c.text}</span>
                                    <button className="comment-delete-btn" onClick={() => removeComment(ann.id, c.id)}>
                                        <X size={10} strokeWidth={3} />
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
                
                <div className="comment-input-row" style={{ marginTop: '4px' }}>
                    <textarea 
                        className="segment-note"
                        placeholder="Add a comment..."
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        rows={1}
                        style={{ minHeight: '32px' }}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                if (draft.trim()) {
                                    addComment(ann.id, draft.trim());
                                    setDraft('');
                                }
                            }
                        }}
                    />
                    <button 
                        className="post-btn" 
                        disabled={!draft.trim()}
                        onClick={() => {
                            addComment(ann.id, draft.trim());
                            setDraft('');
                        }}
                    >
                        Post
                    </button>
                </div>
            </div>
        </div>
    );
}

function Panel({ plane, label }) {
  const { 
    coords, zoom, annotations, setCoords, 
    activeTool, currentColor, addAnnotation, updateLastAnnotation,
    removeAnnotation, updateAnnotation, setActiveTab, isSidebarOpen, toggleSidebar,
    activePopup, setActivePopup, filters, isEnhanced
  } = useStore();
  
  const [isDrawing, setIsDrawing] = useState(false);
  const panelRef = useRef(null);

  const getImgCoords = (e) => {
    const rect = panelRef.current.getBoundingClientRect();
    let dx = (e.clientX - rect.left) - rect.width / 2;
    let dy = (e.clientY - rect.top) - rect.height / 2;

    dx = dx / zoom;
    dy = dy / zoom;

    if (plane === 'YZ') {
       const temp = dx; dx = dy; dy = -temp;
    } else if (plane === 'XZ') {
       const temp = dx; dx = -dy; dy = temp;
    }

    return {
      x: coords.x + dx,
      y: coords.y + dy
    };
  };

  const handleMouseDown = (e) => {
    if (activeTool === 'move' || activeTool === 'eraser' || activeTool === 'fill') return;
    const startObj = getImgCoords(e);
    
    setIsDrawing(true);
    
    if (activeTool === 'trace') {
      addAnnotation({
        id: Date.now().toString(), type: 'path', color: currentColor, points: [startObj], label: `Path ${annotations.length + 1}`, note: ''
      });
    } else if (activeTool === 'lasso') {
      addAnnotation({
        id: Date.now().toString(), type: 'lasso', color: currentColor, fillColor: 'transparent', points: [startObj], label: `Segment ${annotations.length + 1}`, note: ''
      });
    } else if (activeTool === 'bbox') {
      addAnnotation({
        id: Date.now().toString(), type: 'bbox', color: currentColor, points: [startObj, startObj], label: `Bounds ${annotations.length + 1}`, note: '', timestamp: new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      });
    } else if (activeTool === 'smart') {
      addAnnotation({
        id: Date.now().toString(), type: 'smart', color: currentColor, points: [startObj, startObj], label: `AI Segment ${annotations.length + 1}`, note: '', timestamp: new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      });
    } else if (activeTool === 'pin') {
      const newId = Date.now().toString();
      addAnnotation({
        id: newId, type: 'pin', x: startObj.x, y: startObj.y, color: currentColor, label: `Pin ${annotations.filter(a=>a.type==='pin').length + 1}`, comments: [], timestamp: new Date().toLocaleString('en-US'), isDraft: true
      });
      setActivePopup(newId);
      setActiveTool('move'); // Auto-revert to move tool after dropping a pin
    }
  };

  const handleMouseMove = (e) => {
    if (activeTool === 'move' && e.buttons === 1) {
      setCoords({
        x: coords.x - e.movementX / zoom,
        y: coords.y - e.movementY / zoom,
      });
      return;
    }

    if (!isDrawing) return;
    const curObj = getImgCoords(e);

    if (activeTool === 'trace' || activeTool === 'lasso') {
      updateLastAnnotation((ann) => ({
        ...ann, points: [...ann.points, curObj]
      }));
    } else if (activeTool === 'bbox' || activeTool === 'smart') {
      updateLastAnnotation((ann) => ({
        ...ann, points: [ann.points[0], curObj]
      }));
    }
  };

  const handleMouseUp = () => {
    if (!isDrawing) return;
    setIsDrawing(false);

    if (activeTool === 'lasso') {
      updateLastAnnotation((ann) => {
        if (!ann.points || ann.points.length === 0) return ann;
        return { ...ann, points: [...ann.points, ann.points[0]] };
      });
    }

    if (activeTool === 'smart') {
      const ann = useStore.getState().annotations[useStore.getState().annotations.length - 1];
      if (ann && ann.type === 'smart' && ann.points.length === 2) {
         const mediaEl = panelRef.current.querySelector('.brain-media');
         if (!mediaEl) return;
         
         const [p1, p2] = ann.points;
         const x = Math.min(p1.x, p2.x), y = Math.min(p1.y, p2.y);
         const w = Math.abs(p2.x - p1.x), h = Math.abs(p2.y - p1.y);
         if (w < 5 || h < 5) { removeAnnotation(ann.id); return; }

         try {
             const naturalW = mediaEl.naturalWidth || mediaEl.videoWidth;
             const naturalH = mediaEl.naturalHeight || mediaEl.videoHeight;
             const clientW = mediaEl.clientWidth, clientH = mediaEl.clientHeight;
             
             const imgRatio = naturalW / naturalH;
             const containerRatio = clientW / clientH;
             let renderW, renderH, offsetX = 0, offsetY = 0;
             if (imgRatio > containerRatio) {
                 renderW = clientW; renderH = clientW / imgRatio; offsetY = (clientH - renderH) / 2;
             } else {
                 renderH = clientH; renderW = clientH * imgRatio; offsetX = (clientW - renderW) / 2;
             }
             
             const toNatX = (px) => ((px - offsetX) / renderW) * naturalW;
             const toNatY = (py) => ((py - offsetY) / renderH) * naturalH;
             
             const nx1 = toNatX(x), ny1 = toNatY(y);
             const nx2 = toNatX(x+w), ny2 = toNatY(y+h);
             const startX = Math.max(0, Math.floor(nx1)), startY = Math.max(0, Math.floor(ny1));
             const endX = Math.min(naturalW, Math.floor(nx2)), endY = Math.min(naturalH, Math.floor(ny2));
             const bw = endX - startX, bh = endY - startY;

             const cvs = document.createElement('canvas');
             cvs.width = naturalW; cvs.height = naturalH;
             const ctx = cvs.getContext('2d');
             ctx.drawImage(mediaEl, 0, 0, naturalW, naturalH);
             
             const imgData = ctx.getImageData(startX, startY, bw, bh);
             let maxB = 0;
             for (let i = 0; i < imgData.data.length; i += 4) {
                 const lum = imgData.data[i] * 0.299 + imgData.data[i+1] * 0.587 + imgData.data[i+2] * 0.114;
                 if (lum > maxB) maxB = lum;
             }
             const thresh = maxB * 0.25; 
             
             const maskCvs = document.createElement('canvas');
             maskCvs.width = bw; maskCvs.height = bh;
             const mCtx = maskCvs.getContext('2d');
             const mData = mCtx.createImageData(bw, bh);
             const [rCol, gCol, bCol] = hexToRgb(ann.color);
             
             let hasPixels = false;
             for (let i = 0; i < imgData.data.length; i += 4) {
                 const lum = imgData.data[i] * 0.299 + imgData.data[i+1] * 0.587 + imgData.data[i+2] * 0.114;
                 if (lum > thresh) {
                     mData.data[i] = rCol; mData.data[i+1] = gCol; mData.data[i+2] = bCol; mData.data[i+3] = 200;
                     hasPixels = true;
                 } else { mData.data[i+3] = 0; }
             }
             if (!hasPixels) { removeAnnotation(ann.id); return; }
             mCtx.putImageData(mData, 0, 0);
             
             updateAnnotation(ann.id, {
                 type: 'smart-mask', maskUrl: maskCvs.toDataURL(), maskX: x, maskY: y, maskW: w, maskH: h
             });
         } catch(e) { removeAnnotation(ann.id); }
      }
    }
  };

  const renderAnnotation = (ann) => {
    const isEraser = activeTool === 'eraser';
    const isFill = activeTool === 'fill';

    const clickProps = {
        style: { pointerEvents: 'all' },
        onDoubleClick: (e) => {
            e.stopPropagation();
            setActiveTab('Segments');
            if (!isSidebarOpen) toggleSidebar();
            setTimeout(() => {
                const noteInput = document.getElementById(`segment-note-${ann.id}`);
                if (noteInput) {
                    noteInput.focus();
                    noteInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }, 100);
        },
        onClick: (e) => {
            if (ann.type === 'pin') {
               e.stopPropagation();
               setActivePopup(ann.id);
            }
        },
        onContextMenu: (e) => {
            e.preventDefault();
            e.stopPropagation();
            const pos = getImgCoords(e);
            useStore.getState().setContextMenu({ 
               x: e.clientX, y: e.clientY, 
               imgX: pos.x, imgY: pos.y, 
               targetId: ann.id 
            });
        }
    };

    if (isEraser) {
        clickProps.onClick = (e) => { e.stopPropagation(); removeAnnotation(ann.id); };
        clickProps.style.cursor = 'pointer';
    } else if (isFill && ann.type === 'lasso') {
        clickProps.onClick = (e) => {
            e.stopPropagation();
            updateAnnotation(ann.id, { fillColor: currentColor + '66', color: currentColor });
        };
        clickProps.style.cursor = 'pointer';
    }

    if (ann.type === 'pin') {
        return (
          <circle key={ann.id} cx={ann.x} cy={ann.y} r={8/zoom} fill={ann.color} stroke="white" strokeWidth={2/zoom} {...clickProps} style={{...clickProps.style, cursor: 'pointer', filter: 'drop-shadow(0 0 2px rgba(0,0,0,0.5))'}} />
        );
    }

    if (ann.type === 'bbox' && ann.points && ann.points.length === 2) {
      const [p1, p2] = ann.points;
      const x = Math.min(p1.x, p2.x);
      const y = Math.min(p1.y, p2.y);
      const width = Math.abs(p2.x - p1.x);
      const height = Math.abs(p2.y - p1.y);
      return (
         <rect 
            key={ann.id} x={x} y={y} width={width} height={height} 
            fill="rgba(139, 92, 246, 0.1)" stroke={ann.color} strokeWidth={4 / zoom} 
            {...clickProps}
         />
      );
    }
    
    if (ann.type === 'smart' && ann.points && ann.points.length === 2) {
      const [p1, p2] = ann.points;
      const x = Math.min(p1.x, p2.x), y = Math.min(p1.y, p2.y);
      const width = Math.abs(p2.x - p1.x), height = Math.abs(p2.y - p1.y);
      return (
         <rect 
            key={ann.id} x={x} y={y} width={width} height={height} 
            fill="rgba(255, 215, 0, 0.1)" stroke="gold" strokeWidth={3 / zoom} strokeDasharray="6 6"
            {...clickProps} style={{...clickProps.style, pointerEvents: 'none'}}
         />
      );
    }
    
    if (ann.type === 'smart-mask') {
      return (
         <g key={ann.id} {...clickProps} style={{ ...clickProps.style, cursor: isEraser ? 'crosshair' : 'pointer' }}>
             <image href={ann.maskUrl} x={ann.maskX} y={ann.maskY} width={ann.maskW} height={ann.maskH} preserveAspectRatio="none" style={{ imageRendering: 'pixelated' }} />
             <rect x={ann.maskX} y={ann.maskY} width={ann.maskW} height={ann.maskH} fill="transparent" stroke={ann.color} strokeWidth={2/zoom} strokeDasharray={`${4/zoom} ${6/zoom}`} />
         </g>
      );
    }

    if ((ann.type === 'path' || ann.type === 'lasso') && ann.points && ann.points.length > 0) {
      const d = `M ${ann.points.map(p => `${p.x},${p.y}`).join(' L ')}`;
      const fillAttr = ann.type === 'lasso' ? ann.fillColor : 'none';
      return (
        <path key={ann.id} d={d} fill={fillAttr} stroke={ann.color} strokeWidth={4 / zoom} strokeLinecap="round" strokeLinejoin="round" {...clickProps} />
      );
    }
    return null;
  };

  const { mediaUrl, mediaType } = useStore();
  const transformString = `translate(50%, 50%) scale(${zoom}) ${plane==='YZ' ? 'rotate(90deg)' : plane==='XZ' ? 'rotate(-90deg)' : ''} translate(${-coords.x}px, ${-coords.y}px)`;
  const filterString = `brightness(${filters.brightness}%) contrast(${filters.contrast}%) invert(${filters.invert ? 100 : 0}%)`;

  let modeClass = 'move-mode';
  if (activeTool === 'trace') modeClass = 'trace-mode';
  if (activeTool === 'lasso') modeClass = 'lasso-mode';
  if (activeTool === 'fill') modeClass = 'fill-mode';
  if (activeTool === 'eraser') modeClass = 'erase-mode';
  if (activeTool === 'bbox') modeClass = 'bbox-mode';
  if (activeTool === 'smart') modeClass = 'smart-mode';
  if (activeTool === 'pin') modeClass = 'trace-mode'; // gives crosshair

  const mediaClass = `brain-media ${isEnhanced ? 'enhanced-media' : ''}`;

  return (
    <div 
      className={`panel ${modeClass}`}
      ref={panelRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const pos = getImgCoords(e);
          useStore.getState().setContextMenu({ 
             x: e.clientX, y: e.clientY, 
             imgX: pos.x, imgY: pos.y, 
             targetId: null 
          });
      }}
    >
      <div className="panel-header">
        <span className={`panel-label ${plane.toLowerCase()}`}>
          <Layers size={14} /> {label}
        </span>
      </div>
      
      <div className="panel-content">
        <div 
            className="media-layer"
            style={{ transform: transformString, filter: filterString }}
        >
            {mediaType === 'image' ? (
                <img src={mediaUrl} alt="brain slice" className={mediaClass} draggable={false} />
            ) : (
                <video src={mediaUrl} className={mediaClass} autoPlay loop muted playsInline />
            )}
            
            <svg className="svg-layer" style={{ overflow: 'visible', pointerEvents: 'none' }}>
                {annotations.map(renderAnnotation)}
            </svg>
            
            {annotations.filter(a => a.id === activePopup).map(ann => (
                 <CanvasPopup key={`popup-${ann.id}`} ann={ann} plane={plane} zoom={zoom} />
            ))}
        </div>

        {plane === 'XY' && <RemoteCursor />}

        {activeTool === 'move' && (
          <div className="crosshair">
             <div className="crosshair-center"></div>
          </div>
        )}
        
      </div>
    </div>
  );
}

function RemoteCursor() {
   const { remoteCursor, coords, zoom } = useStore();
   return (
     <motion.div className="remote-cursor" animate={{ left: `calc(50% + ${(remoteCursor.x - coords.x) * zoom}px)`, top: `calc(50% + ${(remoteCursor.y - coords.y) * zoom}px)` }} transition={{ type: "spring", stiffness: 100, damping: 20 }}>
       <MousePointer2 className="cursor-icon animate-pulse-glow" size={16} fill="var(--accent-green)" />
       <div className="cursor-label">USER 2</div>
     </motion.div>
   );
}

function GlobalNav() {
  const { coords, zoom, mediaUrl, mediaType, filters } = useStore();
  const boxSize = 100 / Math.max(zoom, 0.5);
  const top = ((coords.y % 2000) / 2000) * 100 - (boxSize/2);
  const left = ((coords.x % 2000) / 2000) * 100 - (boxSize/2);
  const filterString = `brightness(${filters.brightness}%) contrast(${filters.contrast}%) invert(${filters.invert ? 100 : 0}%)`;

  return (
    <div className="global-nav">
      <div className="nav-header">Global Overview</div>
      <div className="nav-content">
         {mediaType === 'image' ? (
             <img src={mediaUrl} className="nav-media" alt="nav" style={{filter: filterString}} />
         ) : (
             <video src={mediaUrl} className="nav-media" autoPlay loop muted playsInline style={{filter: filterString}} />
         )}
         <motion.div 
           className="nav-viewport-box"
           animate={{ width: `${boxSize}%`, height: `${boxSize}%`, top: `${Math.max(0, Math.min(top, 100-boxSize))}%`, left: `${Math.max(0, Math.min(left, 100-boxSize))}%` }}
           transition={{ type: "spring", stiffness: 300, damping: 30 }}
         />
      </div>
    </div>
  );
}

function MeshViewPanel() {
   const { annotations, mediaUrl } = useStore();
   const [rotation, setRotation] = useState({ x: 60, z: -35 });
   
   const handleDrag = (e, info) => {
       setRotation(r => ({
           x: Math.max(0, Math.min(85, r.x - info.delta.y * 0.4)),
           z: r.z + info.delta.x * 0.4
       }));
   };

   return (
       <div className="mesh-fullscreen-view">
           <motion.div 
               className="isometric-scene"
               drag
               dragConstraints={{ top:0, left:0, right:0, bottom:0 }}
               dragElastic={0}
               onDrag={handleDrag}
               animate={{ rotateX: rotation.x, rotateZ: rotation.z }}
               transition={{ type: "tween", ease: "linear", duration: 0 }}
           >
               <div className="iso-layer" style={{ transform: 'translateZ(-140px)' }}>
                   <img src={mediaUrl} className="iso-media" alt="base-layer" draggable={false} />
               </div>
               
               <div className="iso-layer">
                   <svg className="iso-svg" viewBox="0 0 1000 1000">
                     {annotations.map(ann => {
                         if (ann.type === 'path' || ann.type === 'lasso') {
                             const d = `M ${ann.points.map(p => `${p.x/2},${p.y/2}`).join(' L ')}`;
                             return <path key={ann.id} d={d} fill={ann.type==='lasso'?ann.fillColor:'none'} stroke={ann.color} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" />
                         }
                         if (ann.type === 'bbox' && ann.points && ann.points.length===2) {
                             const [p1, p2] = ann.points;
                             return <rect key={ann.id} x={Math.min(p1.x, p2.x)/2} y={Math.min(p1.y, p2.y)/2} width={Math.abs(p2.x - p1.x)/2} height={Math.abs(p2.y - p1.y)/2} fill="rgba(139,92,246,0.15)" stroke={ann.color} strokeWidth={6} />
                         }
                         if (ann.type === 'pin') {
                             return <circle key={ann.id} cx={ann.x/2} cy={ann.y/2} r={16} fill={ann.color} stroke="white" strokeWidth={4} />
                         }
                         return null;
                     })}
                   </svg>
               </div>
               
               <div className="iso-layer" style={{ transform: 'translateZ(140px)', border: '1px dashed rgba(255,255,255,0.1)', background: 'transparent', pointerEvents: 'none' }}>
               </div>
           </motion.div>
           
           <div style={{ position: 'absolute', top: 24, left: 24, zIndex: 50 }}>
               <h2 style={{ fontSize: '24px', fontWeight: 600, color: 'white', textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>3D Mesh Visualization</h2>
               <p style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: '6px' }}>Click and drag to rotate the projection matrices.</p>
           </div>
       </div>
   )
}

function ZoomControl() {
    const { zoom, setZoom } = useStore();
    return (
        <div className="zoom-slider-container">
            <button className="zoom-btn" onClick={() => setZoom(Math.max(zoom - 0.2, 0.2))} title="Zoom Out">
                <ZoomOut size={16} />
            </button>
            <input 
                type="range" 
                className="zoom-slider" 
                min="0.2" max="8" step="0.1" 
                value={zoom} 
                onChange={(e) => setZoom(parseFloat(e.target.value))}
                title="Adjust Magnification"
            />
            <button className="zoom-btn" onClick={() => setZoom(Math.min(zoom + 0.2, 8))} title="Zoom In">
                <ZoomIn size={16} />
            </button>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', minWidth: '36px', textAlign: 'right', fontWeight: 600 }}>
               {Math.round(zoom * 100)}%
            </span>
        </div>
    )
}

function SkeletonTree() {
  return (
    <div className="skeleton-tree">
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '16px', fontSize: '12px', fontWeight: 600, color: 'white' }}>
          Hierarchy Navigator
      </div>
      
      <div className="tree-node">
          <div className="tree-item"><ChevronDown size={14} className="tree-icon"/> <span>Neuron 1 (Soma)</span></div>
          <div className="tree-node">
               <div className="tree-item"><ChevronRight size={14} className="tree-icon"/> <span>Apical Dendrite</span></div>
               <div className="tree-item active"><ChevronDown size={14} className="tree-icon"/> <span>Basal Dendrites</span></div>
               <div className="tree-node">
                    <div className="tree-item"><CircleDashed size={14} className="tree-icon"/> <span>Branch Alpha</span></div>
                    <div className="tree-item"><CircleDashed size={14} className="tree-icon"/> <span>Branch Beta</span></div>
               </div>
               <div className="tree-item"><ChevronRight size={14} className="tree-icon"/> <span>Axon Initial Segment</span></div>
          </div>
      </div>
    </div>
  );
}

function Sidebar() {
    const { 
        isSidebarOpen, activeTab, setActiveTab, 
        annotations, toggleSidebar,
        addComment, removeComment, removeAnnotation
    } = useStore();

    const [drafts, setDrafts] = useState({});

    if (!isSidebarOpen) return null;

    const segments = annotations.filter(a => !a.isDraft && (a.type === 'lasso' || a.type === 'path' || a.type === 'pin' || a.type === 'bbox' || a.type === 'smart-mask'));

    const handlePost = (segId) => {
        const text = drafts[segId]?.trim();
        if (!text) return;
        addComment(segId, text);
        setDrafts(prev => ({ ...prev, [segId]: '' }));
    };

    return (
        <div className="sidebar">
            <div className="sidebar-tabs">
                {['Info', 'Skeleton', 'Comments', 'Segments', 'BBoxes'].map(tab => (
                    <div key={tab} className={`tab ${activeTab === tab ? 'active' : ''}`} onClick={() => setActiveTab(tab)}>
                        {tab}
                    </div>
                ))}
            </div>

            <div className="sidebar-content">
                {activeTab === 'Skeleton' && <SkeletonTree />}
                
                {(activeTab === 'Segments' || activeTab === 'BBoxes') && (
                    <div className="segments-list">
                        <div style={{ display: 'flex', alignItems: 'center', marginBottom: '16px', fontSize: '12px', fontWeight: 600, color: 'white' }}>
                            <ChevronRight size={14} style={{ marginRight: '6px' }} />
                            All Items & Notes
                        </div>

                        {segments.length === 0 ? (
                            <div className="empty-state">
                                No segments or pins yet. Draw a Lasso or drop a Comment Dot to start.
                            </div>
                        ) : (
                            segments.reverse().map(seg => (
                                <div key={seg.id} className="segment-item">
                                    <div className="segment-header">
                                        <div className="segment-title">
                                            <span 
                                                className="segment-color" 
                                                style={seg.type === 'pin' ? { backgroundColor: seg.color, borderRadius: '50%' } : { backgroundColor: seg.color }}
                                            />
                                            {seg.label} {seg.type === 'pin' && '(Dot)'}
                                            {seg.timestamp && <span style={{ fontSize: '10px', color: 'var(--text-muted)', marginLeft: '6px', fontWeight: 'normal' }}>{seg.timestamp}</span>}
                                        </div>
                                        <div style={{ display: 'flex', gap: '4px' }}>
                                            <button 
                                                className="segment-delete-btn" 
                                                title="Delete Item"
                                                onClick={() => removeAnnotation(seg.id)}
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </div>
                                    
                                    {seg.comments && seg.comments.length > 0 && (
                                        <div className="comment-list">
                                            {seg.comments.map(c => (
                                                <div key={c.id} className="comment-bubble" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px' }}>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '10px', color: 'var(--text-muted)' }}>
                                                        <span style={{ fontWeight: 600 }}>{c.author}</span>
                                                        <span>{c.timestamp}</span>
                                                    </div>
                                                    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                                                        <span className="comment-text" style={{ padding: 0 }}>{c.text}</span>
                                                        <button className="comment-delete-btn" onClick={() => removeComment(seg.id, c.id)}><X size={12} strokeWidth={3} /></button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    <div className="comment-input-row">
                                        <textarea 
                                            id={`segment-note-${seg.id}`}
                                            className="segment-note"
                                            placeholder="Write a comment..."
                                            value={drafts[seg.id] || ''}
                                            onChange={(e) => setDrafts({ ...drafts, [seg.id]: e.target.value })}
                                            rows={2}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handlePost(seg.id); }
                                            }}
                                        />
                                        <button className="post-btn" disabled={!(drafts[seg.id]?.trim())} onClick={() => handlePost(seg.id)}>Post</button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}

function ContextMenuOverlay() {
   const { contextMenu, closeContextMenu, setActiveTab, toggleSidebar, isSidebarOpen, addAnnotation, setActivePopup, currentColor, annotations } = useStore();
   
   useEffect(() => {
       const handleGlobalClick = () => { if (contextMenu) closeContextMenu(); };
       window.addEventListener('click', handleGlobalClick);
       return () => window.removeEventListener('click', handleGlobalClick);
   }, [contextMenu, closeContextMenu]);

   if (!contextMenu) return null;

   const handleCommentSidebar = (e) => {
       e.stopPropagation();
       setActiveTab('Segments');
       if (!isSidebarOpen) toggleSidebar();
       if (contextMenu.targetId) {
            setTimeout(() => {
                const noteInput = document.getElementById(`segment-note-${contextMenu.targetId}`);
                if (noteInput) { noteInput.focus(); noteInput.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
            }, 100);
       }
       closeContextMenu();
   };
   
   const handleDropDot = (e) => {
       e.stopPropagation();
       const newId = Date.now().toString();
       addAnnotation({
           id: newId, type: 'pin', x: contextMenu.imgX, y: contextMenu.imgY, color: currentColor,
           label: `Pin ${annotations.filter(a=>a.type==='pin').length + 1}`, comments: [],
           timestamp: new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
           isDraft: true
       });
       setActivePopup(newId);
       closeContextMenu();
   };

   return (
       <div className="custom-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }}>
           {contextMenu.targetId ? (
               <div className="context-item clickable" onClick={handleCommentSidebar}>
                   <MessageSquarePlus className="context-icon" size={16} /> Comment in Sidebar
               </div>
           ) : null}
           {contextMenu.targetId && <div className="context-divider"></div>}
           <div className="context-item clickable" onClick={handleDropDot}>
               <Pointer className="context-icon" size={16} /> Drop Comment Dot Here
           </div>
       </div>
   );
}

export default function App() {
  const { 
    coords, zoom, setZoom, 
    activeTool, setActiveTool, 
    currentColor, setCurrentColor,
    setMedia, isSidebarOpen, toggleSidebar,
    undoAnnotation, pastAnnotations, isMeshView, toggleMeshView,
    annotations, saveDrafts, isEnhanced, toggleEnhance
  } = useStore();
  
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    const handleMouseWheel = (e) => {
       if(e.deltaY < 0) setZoom(Math.min(zoom + 0.15, 8));
       else setZoom(Math.max(zoom - 0.15, 0.2));
    };
    window.addEventListener('wheel', handleMouseWheel, { passive: false });
    return () => window.removeEventListener('wheel', handleMouseWheel);
  }, [zoom, setZoom]);

  const handleFileUpload = (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      const isVideo = file.type.startsWith('video/');
      setMedia(url, isVideo ? 'video' : 'image');
      e.target.value = ''; 
  };

  return (
    <div className="mag-container" onContextMenu={(e) => e.preventDefault()}>
      {/* Invisible global SVG definition for AI Sharpen effect */}
      <svg width="0" height="0" style={{ position: 'absolute' }}>
        <filter id="ai-sharpen">
          <feConvolveMatrix order="3 3" preserveAlpha="true" kernelMatrix="0 -1 0 -1 5 -1 0 -1 0" />
        </filter>
      </svg>
      <ContextMenuOverlay />
      {showSettings && <DatasetSettings onClose={() => setShowSettings(false)} />}
      
      <header className="top-bar">
        <div className="logo">
           <Focus className="logo-icon" size={24} />
           <span className="logo-text">MagNeurific</span>
        </div>
        
        <div className="toolbar" style={{ marginLeft: 'auto', marginRight: '24px' }}>
           {annotations.some(a => a.isDraft) && (
               <>
                 <button 
                    className="tool-btn" 
                    onClick={saveDrafts} 
                    style={{ background: 'var(--accent-blue)', color: 'white', padding: '6px 16px', borderRadius: '4px', gap: '6px', fontSize: '13px', fontWeight: 600, width: 'auto' }}
                 >
                    <Save size={16} /> Save Context
                 </button>
                 <div style={{ width: '1px', height: '20px', background: 'var(--border-color)', margin: '0 8px' }} />
               </>
           )}
           <label className="tool-btn" title="Upload Media (PNG/Video)">
             <Upload size={18} />
             <input type="file" accept="image/*,video/*" onChange={handleFileUpload} />
           </label>
           
           <div style={{ width: '1px', height: '20px', background: 'var(--border-color)', margin: '0 8px' }} />

           <button className={`tool-btn ${activeTool === 'move' ? 'active' : ''}`} onClick={() => setActiveTool('move')} title="Pan / Move">
             <MousePointer2 size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'trace' ? 'active' : ''}`} onClick={() => setActiveTool('trace')} title="Path Trace (Open)">
             <PenTool size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'lasso' ? 'active' : ''}`} onClick={() => setActiveTool('lasso')} title="Lasso Trace (Closed Segment)">
             <CircleDashed size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'pin' ? 'active' : ''}`} onClick={() => setActiveTool('pin')} title="Drop Pin Comment">
             <MapPin size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'smart' ? 'active' : ''}`} onClick={() => setActiveTool('smart')} title="Machine Vision Extract (Box a Cell)">
             <Wand2 size={18} fill={activeTool === 'smart' ? 'gold' : 'none'} color={activeTool === 'smart' ? 'gold' : 'currentcolor'} />
           </button>
           <button className={`tool-btn ${activeTool === 'bbox' ? 'active' : ''}`} onClick={() => setActiveTool('bbox')} title="3D Bounding Box">
             <BoxSelect size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'fill' ? 'active' : ''}`} onClick={() => setActiveTool('fill')} title="Fill Lasso Segment">
             <PaintBucket size={18} />
           </button>
           <button className={`tool-btn ${activeTool === 'eraser' ? 'active' : ''}`} onClick={() => setActiveTool('eraser')} title="Eraser">
             <Eraser size={18} />
           </button>
           
           <div style={{ width: '1px', height: '20px', background: 'var(--border-color)', margin: '0 8px' }} />

           <button className={`tool-btn ${isEnhanced ? 'active' : ''}`} onClick={toggleEnhance} title="AI Super Resolution (Sharpen & Smooth)">
             <Sparkles size={18} />
           </button>

           <div style={{ width: '1px', height: '20px', background: 'var(--border-color)', margin: '0 8px' }} />

           <button 
             className="tool-btn" 
             onClick={undoAnnotation} 
             title="Undo (Revert last edit)"
             disabled={pastAnnotations.length === 0}
             style={{ opacity: pastAnnotations.length === 0 ? 0.3 : 1, cursor: pastAnnotations.length === 0 ? 'not-allowed' : 'pointer' }}
           >
             <Undo2 size={18} />
           </button>
           
           <div style={{ width: '1px', height: '20px', background: 'var(--border-color)', margin: '0 8px' }} />

           <button 
               className={`tool-btn ${isMeshView ? 'active' : ''}`} 
               onClick={toggleMeshView} 
               title="Toggle 3D Mesh View"
               style={{ padding: '4px 12px', fontSize: '12px', fontWeight: 600, letterSpacing: '0.5px' }}
           >
             <Box size={14} style={{ marginRight: '6px' }} />
             {isMeshView ? 'EXIT MESH' : 'MESH VIEW'}
           </button>

           <label className="tool-btn" title="Highlight Color">
             <div className="color-indicator" style={{ backgroundColor: currentColor }}></div>
             <input type="color" value={currentColor} onChange={(e) => setCurrentColor(e.target.value)} />
           </label>
        </div>

        <div className="coord-display">
          X: {Math.round(coords.x)} Y: {Math.round(coords.y)} Z: {Math.round(coords.z)}
        </div>

        <div className="user-indicators" style={{ marginLeft: '24px' }}>
          <button className="tool-btn" onClick={() => setShowSettings(!showSettings)} title="Dataset Settings">
            <SlidersHorizontal size={18} />
          </button>
          <div className="user-badge u1" style={{ marginLeft: 8 }}>USER 1</div>
          <button className="tool-btn" onClick={toggleSidebar} title="Toggle Sidebar">
            <SidebarIcon size={18} />
          </button>
        </div>
      </header>

      <div className="app-body">
        <main className="main-content">
            <GlobalNav />
            <ZoomControl />
            
            {isMeshView ? (
                <MeshViewPanel />
            ) : (
                <div className="grid-layout">
                    <Panel plane="XY" label="XY Plane" />
                    <Panel plane="YZ" label="YZ Plane" />
                    <Panel plane="XZ" label="XZ Plane" />
                    
                    <div className="panel three-d-panel">
                        <div className="panel-header">
                            <span className="panel-label"><Layers size={14} /> 3D Volume</span>
                        </div>
                        <div style={{ textAlign: 'center', maxWidth: '240px' }}>
                            <Layers size={48} style={{ opacity: 0.2, margin: '0 auto 16px', display: 'block' }} />
                            <p>3D WebGL Volume Render</p>
                            <p style={{ fontSize: '13px', marginTop: '12px', color: 'var(--accent-blue)', fontWeight: 500 }}>
                            Syncing segments & meshes...
                            </p>
                        </div>
                    </div>
                </div>
            )}
        </main>
        
        <Sidebar />
      </div>

    </div>
  );
}
