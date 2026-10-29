import React, {useState} from 'react';

const states=['ABIERTO','PRÓXIMAMENTE','INSCRIPCIONES FINALIZADAS','POR CONFIRMAR','ARCHIVADO','DEMO'];
const lines=text=>String(text||'').split('\n').map(x=>x.trim()).filter(Boolean);

export function RegistrationCreator({courses,api,onSaved,chat=null,initialOpen=false}){
  const initial = ()=>({name:chat?.registrations?.at(-1)?.name||chat?.name||'',course_id:courses.some(c=>c.id===chat?.selected_course&&['ABIERTO','PRÓXIMAMENTE'].includes(c.estado))?chat.selected_course:'',...Object.fromEntries(['identification','phone','email'].map(k=>[k,chat?.registrations?.at(-1)?.[k]||chat?.registration_draft?.[k]||'']))});
  const [open,setOpen]=useState(initialOpen),[form,setForm]=useState(initial),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const eligible=courses.filter(c=>['ABIERTO','PRÓXIMAMENTE'].includes(c.estado));
  const change=(key,value)=>setForm({...form,[key]:value});
  return <div className="management-create"><button className="primary" onClick={()=>setOpen(!open)}>{open?'Cerrar formulario':'+ Inscribir persona'}</button>{open&&<form className="management-form" onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');try{await api('/registrations',{...form,...(chat?{chat_id:chat.id}:{})});await onSaved();setForm(initial());setOpen(false);}catch(e){setError(e.message);}finally{setBusy(false);}}}>
    <h3>Nueva inscripción · pendiente de pago</h3><label>Curso<select required value={form.course_id||''} onChange={e=>change('course_id',e.target.value)}><option value="">Selecciona un curso abierto o próximo</option>{eligible.map(c=><option key={c.id} value={c.id}>{c.titulo}</option>)}</select></label>
    {!eligible.length&&<p>No hay cursos habilitados. Revisa su estado en Catálogo de cursos.</p>}
    {[['name','Nombres y apellidos','text'],['identification','Cédula (10 dígitos)','text'],['phone','Teléfono','tel'],['email','Correo','email']].map(([key,label,type])=><label key={key}>{label}<input type={type} required maxLength={key==='name'?120:key==='email'?254:20} value={form[key]||''} onChange={e=>change(key,e.target.value)}/></label>)}
    {error&&<p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy||!eligible.length}>Guardar inscripción pendiente</button>
  </form>}</div>;
}

export function CourseManager({courses,api,onSaved}){
  const [query,setQuery]=useState(''),[status,setStatus]=useState('all'),[editor,setEditor]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const visible=courses.filter(c=>(status==='all'||c.estado===status)&&[c.titulo,c.contenido,c.details?.modalidad].join(' ').toLowerCase().includes(query.toLowerCase()));
  function edit(course={titulo:'',estado:'POR CONFIRMAR',duracion:'',contenido:'',enlace:'',details:{},is_demo:false}){
    const d=course.details||{};setError('');setEditor({...course,details:{...d},materials:(d.materiales||[]).join('\n'),modules:(d.modulos||[]).join('\n'),prices:(d.precios||[]).map(p=>p.perfil+' | '+p.valor).join('\n')});
  }
  const field=(key,value)=>setEditor(old=>({...old,[key]:value}));
  const detail=(key,value)=>setEditor(old=>({...old,details:{...old.details,[key]:value}}));
  async function save(e){e.preventDefault();setBusy(true);setError('');try{
    const prices=lines(editor.prices).map(row=>{const [perfil,valor]=row.split('|').map(s=>s.trim());if(!perfil||!/^\d+(\.\d{1,2})?$/.test(valor||''))throw new Error('Cada tarifa debe tener formato: Público | 120.00');return {perfil,valor,moneda:'USD'};});
    await api('/courses'+(editor.id?'/'+editor.id:''),{titulo:editor.titulo,estado:editor.estado,duracion:editor.duracion,contenido:editor.contenido,enlace:editor.enlace,is_demo:editor.is_demo,details:{...editor.details,materiales:lines(editor.materials),modulos:lines(editor.modules),precios:prices}});await onSaved();setEditor(null);
  }catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="catalog"><div className="management-toolbar"><h2>Catálogo · {visible.length} de {courses.length} cursos</h2><button className="primary" onClick={()=>edit()}>+ Agregar curso</button></div><div className="management-toolbar"><input aria-label="Buscar cursos" placeholder="Buscar por nombre, descripción o modalidad…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Filtrar cursos por estado" value={status} onChange={e=>setStatus(e.target.value)}><option value="all">Todos los estados</option>{states.map(s=><option key={s}>{s}</option>)}</select></div>
    {editor&&<form className="management-form course-editor" onSubmit={save}><h3>{editor.id?'Editar curso':'Agregar curso'}</h3>
      <label>Título<input required maxLength={250} value={editor.titulo||''} onChange={e=>field('titulo',e.target.value)}/></label><label>Estado<select value={editor.estado} onChange={e=>field('estado',e.target.value)}>{states.map(s=><option key={s}>{s}</option>)}</select></label>
      <label>Duración<input value={editor.duracion||''} onChange={e=>field('duracion',e.target.value)}/></label><label>Enlace de la ficha<input type="url" value={editor.enlace||''} onChange={e=>field('enlace',e.target.value)}/></label>
      <label>Descripción<textarea value={editor.contenido||''} onChange={e=>field('contenido',e.target.value)}/></label><label>Requisitos y detalles completos<textarea rows={7} value={editor.details.requisitos_detalles_texto||''} onChange={e=>detail('requisitos_detalles_texto',e.target.value)}/></label>
      {[['requisitos','Conocimientos previos'],['horario','Horario'],['modalidad','Modalidad'],['lugar','Lugar'],['cupo','Cupo publicado'],['certificado','Certificado'],['registration_url','Enlace oficial de inscripción']].map(([key,label])=><label key={key}>{label}<textarea value={editor.details[key]||''} onChange={e=>detail(key,e.target.value)}/></label>)}
      {[['registration_start','Inicio inscripciones'],['registration_end','Fin inscripciones'],['course_start','Inicio curso'],['course_end','Fin curso']].map(([key,label])=><label key={key}>{label}<input type="date" value={editor.details[key]||''} onChange={e=>detail(key,e.target.value)}/></label>)}
      <label>Materiales (uno por línea)<textarea rows={4} value={editor.materials} onChange={e=>field('materials',e.target.value)}/></label><label>Contenido y módulos (uno por línea)<textarea rows={6} value={editor.modules} onChange={e=>field('modules',e.target.value)}/></label><label>Tarifas en USD (Perfil | 120.00, una por línea)<textarea rows={4} value={editor.prices} onChange={e=>field('prices',e.target.value)}/></label>
      <label><input type="checkbox" checked={editor.is_demo} onChange={e=>field('is_demo',e.target.checked)}/> Datos de demostración</label>
      <p>El estado ABIERTO o PRÓXIMAMENTE habilita solicitudes. Conserva las fechas oficiales; archivar un curso no elimina sus inscripciones.</p>{error&&<p className="error" role="alert">{error}</p>}<div className="management-toolbar"><button className="primary" disabled={busy}>Guardar curso</button><button type="button" className="secondary" onClick={()=>setEditor(null)}>Cancelar</button></div>
    </form>}
    {!visible.length&&<p>No hay cursos que coincidan con la búsqueda.</p>}
    {visible.map(c=><article key={c.id}><div><h3>{c.titulo}</h3><p>{c.contenido}</p><span>{c.duracion} · {c.estado}</span><details><summary>Requisitos y detalles completos</summary><p className="source-details">{c.details?.requisitos_detalles_texto||c.details?.requisitos||'No especificados en la fuente.'}</p><p><strong>Materiales:</strong> {(c.details?.materiales||[]).join('; ')}</p><p><strong>Certificado:</strong> {c.details?.certificado}</p>{c.enlace&&<a href={c.enlace} target="_blank" rel="noopener noreferrer">Ver ficha fuente</a>}</details></div><button className="secondary" onClick={()=>edit(c)}>Editar curso</button></article>)}
  </section>;
}
