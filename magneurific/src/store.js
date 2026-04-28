import { create } from 'zustand';

export const useStore = create((set) => ({
  coords: { x: 512, y: 512, z: 128 },
  zoom: 1.5,
  activeUserId: "u1",
  
  activeTool: 'move', // 'move', 'trace', 'lasso', 'fill', 'eraser', 'bbox', 'smart'
  currentColor: '#10b981',
  
  mediaUrl: '/brain_em_slice.png',
  mediaType: 'image',
  
  isSidebarOpen: true,
  activeTab: 'Segments',
  isEnhanced: false,
  isMeshView: false,
  
  annotations: [],
  pastAnnotations: [],
  remoteCursor: { x: 500, y: 600 },
  
  // Settings & Filters
  filters: { brightness: 100, contrast: 100, invert: false },
  setFilters: (newFilters) => set((state) => ({ filters: { ...state.filters, ...newFilters } })),

  // Popups & Context Menus
  activePopup: null,
  setActivePopup: (id) => set({ activePopup: id }),
  contextMenu: null,
  setContextMenu: (menuProps) => set({ contextMenu: menuProps }),
  closeContextMenu: () => set({ contextMenu: null }),

  setCoords: (partialCoords) => set((state) => ({ 
    coords: { ...state.coords, ...partialCoords } 
  })),
  
  setZoom: (zoomLevel) => set({ zoom: zoomLevel }),
  updateRemoteCursor: (pos) => set({ remoteCursor: pos }),
  setActiveTool: (tool) => set({ activeTool: tool }),
  setCurrentColor: (color) => set({ currentColor: color }),
  
  toggleSidebar: () => set((state) => ({ isSidebarOpen: !state.isSidebarOpen })),
  setActiveTab: (tab) => set({ activeTab: tab }),
  toggleEnhance: () => set((state) => ({ isEnhanced: !state.isEnhanced })),
  toggleMeshView: () => set((state) => ({ isMeshView: !state.isMeshView })),
  
  setMedia: (url, type) => set({ 
      mediaUrl: url, 
      mediaType: type,
      coords: { x: 500, y: 500, z: 128 },
      zoom: 1.0
  }),
  
  addAnnotation: (ann) => set((state) => ({ 
    pastAnnotations: [...state.pastAnnotations, state.annotations],
    annotations: [...state.annotations, ann] 
  })),
  
  undoAnnotation: () => set((state) => {
    if (state.pastAnnotations.length === 0) return state;
    const previous = state.pastAnnotations[state.pastAnnotations.length - 1];
    return {
       annotations: previous,
       pastAnnotations: state.pastAnnotations.slice(0, -1)
    };
  }),
  
  removeAnnotation: (id) => set((state) => ({
    pastAnnotations: [...state.pastAnnotations, state.annotations],
    annotations: state.annotations.filter(a => a.id !== id)
  })),
  
  updateLastAnnotation: (updateFn) => set((state) => {
    if (state.annotations.length === 0) return state;
    const newAnns = [...state.annotations];
    const lastIdx = newAnns.length - 1;
    newAnns[lastIdx] = updateFn(newAnns[lastIdx]);
    return { annotations: newAnns };
  }),
  
  updateAnnotation: (id, updates) => set((state) => ({
    annotations: state.annotations.map(a => a.id === id ? { ...a, ...updates } : a)
  })),

  // Comments System
  addComment: (annId, text) => set((state) => ({
    annotations: state.annotations.map(a => {
        if (a.id === annId) {
            const comments = a.comments || [];
            return {
                ...a,
                isDraft: false,
                comments: [...comments, { 
                    id: Date.now().toString(), 
                    text, 
                    author: state.activeUserId, 
                    timestamp: new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})
                }]
            };
        }
        return a;
    })
  })),

  removeComment: (annId, commentId) => set((state) => ({
    annotations: state.annotations.map(a => {
        if (a.id === annId && a.comments) {
            return { ...a, comments: a.comments.filter(c => c.id !== commentId) };
        }
        return a;
    })
  }))
}));
