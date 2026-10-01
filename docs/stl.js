// Local STL import, using the desktop model's centering and cyan material.
let customModel=null,stlLoading=false;
const stlTriangleLimit=5000;
function syncModelPickers(){
  document.querySelectorAll('[data-model-picker]').forEach(picker=>{
    picker.replaceChildren(...Array.from(byId('model').options,option=>option.cloneNode(true)));
    picker.value=state.model;
  });
  byId('model').value=state.model;
}
function stlStatus(message){
  byId('stl-status').textContent=message;byId('stl-status').hidden=!message;
  document.querySelectorAll('[data-stl-status]').forEach(node=>node.textContent=message);
}
function parseSTL(buffer){
  const vertices=[],view=new DataView(buffer);
  const addTriangle=points=>{
    if(vertices.length/3>=stlTriangleLimit)throw Error('Use an STL with 5,000 triangles or fewer for this lightweight demo.');
    if(points.some(point=>point.some(value=>!Number.isFinite(value))))throw Error('The STL contains invalid coordinates.');
    vertices.push(...points);
  };
  const count=buffer.byteLength>=84?view.getUint32(80,true):0;
  if(buffer.byteLength>=84&&84+count*50===buffer.byteLength){
    if(count>stlTriangleLimit)throw Error('Use an STL with 5,000 triangles or fewer for this lightweight demo.');
    for(let face=0;face<count;face++){
      const offset=84+face*50+12;
      addTriangle([0,1,2].map(vertex=>[0,1,2].map(axis=>view.getFloat32(offset+vertex*12+axis*4,true))));
    }
  }else{
    const source=new TextDecoder('utf-8',{fatal:true}).decode(buffer).trim();
    if(!/^solid(?:\s|$)/i.test(source)||!/endsolid(?:\s|$)/i.test(source))throw Error('Choose a valid ASCII or binary STL file.');
    const number='[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?';
    const vertexPattern=new RegExp(`\\bvertex\\s+(${number})\\s+(${number})\\s+(${number})\\s*`,'gi');
    let facets=0;
    for(const facet of source.matchAll(/\bfacet\s+normal\b([\s\S]*?)\bendfacet\b/gi)){
      const points=Array.from(facet[1].matchAll(vertexPattern),match=>match.slice(1,4).map(Number));
      if(points.length!==3||! /\bouter\s+loop\b[\s\S]*\bendloop\b/i.test(facet[1]))throw Error('Each STL facet must contain exactly three vertices.');
      addTriangle(points);facets++;
    }
    if(!facets||Array.from(source.matchAll(/\bvertex\b/gi)).length!==vertices.length||Array.from(source.matchAll(/\bendfacet\b/gi)).length!==facets)throw Error('The STL has incomplete or invalid facets.');
  }
  if(!vertices.length)throw Error('This STL has no triangles.');
  const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
  for(const point of vertices)point.forEach((value,axis)=>{low[axis]=Math.min(low[axis],value);high[axis]=Math.max(high[axis],value);});
  const extent=Math.max(...high.map((value,axis)=>value-low[axis]));
  if(!Number.isFinite(extent)||extent<=0)throw Error('This STL has no usable dimensions.');
  const center=high.map((value,axis)=>value/2+low[axis]/2),scale=12/extent;
  // STL units vary; fit the centered model into the browser's existing view.
  return {vertices:vertices.map(point=>point.map((value,axis)=>(value-center[axis])*scale)),faces:Array.from({length:vertices.length/3},(_,face)=>({indices:[face*3,face*3+1,face*3+2],color:[.25,.75,1,.95]}))};
}
byId('stl-file').onchange=async event=>{
  const file=event.target.files[0];event.target.value='';
  if(!file||stlLoading)return;
  stlLoading=true;stlStatus(`Loading ${file.name}…`);
  try{
    if(file.size>8*1024*1024)throw Error('Choose an STL smaller than 8 MB.');
    const model=parseSTL(await file.arrayBuffer());
    customModel=model;
    let option=byId('model').querySelector('[value="custom"]');
    if(!option){option=document.createElement('option');option.value='custom';byId('model').insertBefore(option,byId('model').querySelector('[value="load-stl"]'));}
    option.textContent=`Custom · ${file.name}`;state.model='custom';syncModelPickers();resetCamera();
    stlStatus(`${file.name} · ${model.faces.length.toLocaleString()} triangles · local only`);
    log('SYS',`CUSTOM STL LOADED · ${model.faces.length} TRIANGLES · LOCAL FILE`);
  }catch(error){syncModelPickers();stlStatus(`STL load failed: ${error.message}`);}
  finally{stlLoading=false;}
};
