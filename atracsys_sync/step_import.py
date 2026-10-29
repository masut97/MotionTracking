"""Compose STEP component occurrences without assuming an exporter direction."""
from pathlib import Path
import re

import numpy as np


def _kind(body, kind):
    return re.match(r'\s*'+kind+r'\s*\(',body,re.I) is not None


def _assembly_products(model):
    """Use STEP occurrence metadata to distinguish parent and child products."""
    products={}
    for entity,body in model.entities.items():
        if _kind(body,'SHAPE_DEFINITION_REPRESENTATION'):
            refs=model.references[entity]
            if len(refs)!=2: continue
            definition=model.references.get(refs[0],())
            if definition:
                products.setdefault(definition[-1],set()).add(refs[1])
    parents={}
    for entity,body in model.entities.items():
        if not _kind(body,'CONTEXT_DEPENDENT_SHAPE_REPRESENTATION'): continue
        context=model.references[entity]
        if len(context)!=2: continue
        definition=model.references.get(context[1],())
        if not definition: continue
        occurrence=definition[-1]
        if not _kind(model.entities.get(occurrence,''),'NEXT_ASSEMBLY_USAGE_OCCURRENCE'): continue
        refs=model.references.get(occurrence,())
        if len(refs)>=2:
            parents[context[0]]=(products.get(refs[-2],set()),products.get(refs[-1],set()))
    return parents


def _aliases(model, representation):
    connections=model.representation_connections()
    result=set();todo=[representation]
    while todo:
        node=todo.pop()
        if node in result: continue
        result.add(node);todo.extend(connections.get(node,()))
    return result


def extract(path, api, direct_extract):
    path=Path(path)
    text=path.read_text(encoding='utf-8',errors='replace')
    model=api._StepModel(text)
    relationships=model.transformed_representation_relationships()
    if not relationships: return direct_extract(path)
    surfaces=model.sphere_surfaces()
    metadata=_assembly_products(model)
    geometry={};aliases={};edges={};children=set();unresolved=False
    def sphere_ids(rep):
        if rep not in geometry: geometry[rep]=model.sphere_ids_reachable_from_representation(rep)
        return geometry[rep]
    def members(rep):
        if rep not in aliases: aliases[rep]=_aliases(model,rep)
        return {ref for item in aliases[rep] for ref in model.references.get(item,())}
    for left,right,transform_id in relationships:
        parent=child=None
        for relation_id,(parent_reps,child_reps) in metadata.items():
            refs=model.references[relation_id]
            if transform_id not in refs: continue
            if left in parent_reps and right in child_reps: parent,child=left,right
            elif right in parent_reps and left in child_reps: parent,child=right,left
        if parent is None:
            # Safe fallback for a flat export without product/occurrence data.
            a,b=bool(sphere_ids(left)),bool(sphere_ids(right))
            if a!=b: parent,child=(right,left) if a else (left,right)
            elif not a:
                unresolved=True
                continue
            else: raise api.StepGeometryError('STEP-Baugruppenrichtung ist nicht eindeutig; keine Geometrie erzeugt.')
        first,second=model.item_transformation(transform_id)
        forward=first in members(parent) and second in members(child)
        reverse=second in members(parent) and first in members(child)
        if forward==reverse:
            raise api.StepGeometryError('STEP-Bezugssysteme lassen sich nicht eindeutig Teil und Baugruppe zuordnen.')
        parent_frame,child_frame=(first,second) if forward else (second,first)
        transform=model.placement(parent_frame)@np.linalg.inv(model.placement(child_frame))
        edges.setdefault(parent,[]).append((child,transform))
        children.add(child)
    if not edges: return direct_extract(path)
    if unresolved:
        raise api.StepGeometryError('STEP-Baugruppenhierarchie ohne eindeutige Eltern-Kind-Zuordnung; bitte vollstaendige Baugruppe exportieren.')
    roots=set(edges)-children
    if len(roots)!=1:
        raise api.StepGeometryError('STEP enthält mehrere unabhängige oder zyklische Baugruppen; bitte eine Baugruppe exportieren.')
    named={rep for rep,name in model.shape_representation_names().items()
           if any(token in name.casefold() for token in api._FIDUCIAL_NAMES)}
    scale=api._length_scale_to_mm(text)
    spheres=[];visited=set()
    def visit(rep,transform,stack):
        if rep in stack: raise api.StepGeometryError('Zyklische STEP-Baugruppe erkannt.')
        visited.add(rep)
        if not named or _aliases(model,rep)&named:
            for entity in sorted(sphere_ids(rep)):
                placement,radius=surfaces[entity]
                local=model.placement(placement)[:3,3]
                center=transform[:3,:3]@local+transform[:3,3]
                if not np.isfinite(center).all() or not np.isfinite(radius) or radius<=0:
                    raise api.StepGeometryError('Ungültige Kugelkoordinaten im STEP.')
                spheres.append(api.SphereCentre(tuple(float(v*scale) for v in center),float(radius*scale),entity))
        for child,local in edges.get(rep,()): visit(child,transform@local,stack|{rep})
    visit(next(iter(roots)),np.eye(4),set())
    if set(edges)-visited:
        raise api.StepGeometryError('Nicht erreichbare STEP-Baugruppenbeziehungen; keine Geometrie erzeugt.')
    if not spheres: raise api.StepGeometryError('Keine Kugelmittelpunkte in den STEP-Baugruppen gefunden.')
    return api.StepExtraction(path.resolve(),api._deduplicate_spheres(spheres),scale,True)


def install():
    from atracsys_tracker import step_geometry as api
    from atracsys_tracker.ui import step_converter_dialog as dialog
    if getattr(api,'_assembly_direction_fixed',False): return
    original=api.extract_sphere_centres
    def corrected(path): return extract(path,api,original)
    corrected.__wrapped__=original
    api.extract_sphere_centres=corrected
    dialog.extract_sphere_centres=corrected
    api._assembly_direction_fixed=True
