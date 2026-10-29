"""Analytic assembly transforms; no licensed parser or real geometry in Git."""
from dataclasses import dataclass
from types import SimpleNamespace as NS

import numpy as np
import pytest

from atracsys_sync.step_import import extract


@dataclass
class Sphere:
    position_mm: tuple
    radius_mm: float
    source_entity: int


@dataclass
class Extraction:
    path: object
    spheres: tuple
    length_scale_to_mm: float
    used_assembly_instances: bool


class Model:
    def __init__(self):
        self.entities={};self.references={};self.frames={};self.relations=[];self.transforms={}
        self.geometry={};self.surfaces={};self.names={}
    def placement(self,i):return self.frames[i]
    def representation_connections(self):return {}
    def sphere_surfaces(self):return self.surfaces
    def sphere_ids_reachable_from_representation(self,i):return self.geometry.get(i,set())
    def shape_representation_names(self):return self.names
    def transformed_representation_relationships(self):return tuple(self.relations)
    def item_transformation(self,i):return self.transforms[i]
    def representation(self,i,name=''):
        self.references.setdefault(i,());self.names[i]=name
        shape=i+10000;definition=i+20000
        self.entities[shape]='PRODUCT_DEFINITION_SHAPE()';self.references[shape]=(definition,)
        self.entities[i+30000]='SHAPE_DEFINITION_REPRESENTATION()';self.references[i+30000]=(shape,i)
    def sphere(self,rep,center=(1,2,3)):
        surface=rep+1000;axis=rep+2000
        self.frames[axis]=matrix(center);self.surfaces[surface]=(axis,3.2)
        self.geometry.setdefault(rep,set()).add(surface)
    def edge(self,parent,child,parent_frame,child_frame,*,fusion=False,metadata=True):
        i=40000+len(self.relations)*10;first=i+1;second=i+2
        self.frames[first]=parent_frame;self.frames[second]=child_frame
        self.references[parent]+= (first,);self.references[child]+=(second,)
        self.transforms[i]=(second,first) if fusion else (first,second)
        self.relations.append((child,parent,i) if fusion else (parent,child,i))
        if metadata:
            self.entities[i+3]='REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION()'
            self.references[i+3]=(parent,child,i)
            self.entities[i+4]='NEXT_ASSEMBLY_USAGE_OCCURRENCE()'
            self.references[i+4]=(parent+20000,child+20000)
            self.entities[i+5]='PRODUCT_DEFINITION_SHAPE()';self.references[i+5]=(i+4,)
            self.entities[i+6]='CONTEXT_DEPENDENT_SHAPE_REPRESENTATION()';self.references[i+6]=(i+3,i+5)


def matrix(translation=(0,0,0),rotation=None):
    m=np.eye(4);m[:3,3]=translation
    if rotation is not None:m[:3,:3]=rotation
    return m


def run(tmp_path,model,scale=1):
    path=tmp_path/'synthetic.step';path.write_text('Synthetic model; no specimen data')
    api=NS(_StepModel=lambda text:model,_length_scale_to_mm=lambda text:scale,
           _FIDUCIAL_NAMES=('fiducial',),SphereCentre=Sphere,StepExtraction=Extraction,
           StepGeometryError=ValueError,_deduplicate_spheres=lambda spheres:tuple(spheres))
    return extract(path,api,lambda p:Extraction(p,(),scale,False))


@pytest.mark.parametrize('fusion',[False,True])
@pytest.mark.parametrize('metadata',[False,True])
def test_both_exporter_directions_keep_occurrences_and_nonzero_local_origin(tmp_path,fusion,metadata):
    m=Model();m.representation(10,'assembly');m.representation(20,'fiducial');m.sphere(20)
    for shift in ((10,0,0),(0,20,0),(0,0,30)):
        m.edge(10,20,matrix(shift),matrix((5,0,0)),fusion=fusion,metadata=metadata)
    result=run(tmp_path,m)
    assert np.allclose([s.position_mm for s in result.spheres],[(6,2,3),(-4,22,3),(-4,2,33)])
    assert result.used_assembly_instances
    assert all(s.radius_mm==3.2 for s in result.spheres)


@pytest.mark.parametrize('fusion',[False,True])
def test_nested_rotated_assembly_composes_to_root_and_scales_units(tmp_path,fusion):
    m=Model();m.representation(10,'assembly');m.representation(20,'subassembly');m.representation(30,'fiducial');m.sphere(30)
    rz=np.array([[0,-1,0],[1,0,0],[0,0,1]])
    m.edge(10,20,matrix((10,0,0),rz),matrix(),fusion=fusion)
    m.edge(20,30,matrix((0,4,0)),matrix(),fusion=not fusion)
    result=run(tmp_path,m,scale=25.4)
    # Root = (10,0,0) + Rz90 * ((0,4,0)+(1,2,3)) = (4,1,3).
    assert np.allclose(result.spheres[0].position_mm,np.array([4,1,3])*25.4)
    assert result.spheres[0].radius_mm==pytest.approx(3.2*25.4)


def test_direct_geometry_still_uses_existing_parser(tmp_path):
    assert run(tmp_path,Model()).used_assembly_instances is False


def test_ambiguous_spherical_parent_and_child_without_metadata_is_rejected(tmp_path):
    m=Model();m.representation(10);m.representation(20);m.sphere(10);m.sphere(20)
    m.edge(10,20,matrix(),matrix(),metadata=False)
    with pytest.raises(ValueError,match='nicht eindeutig'):run(tmp_path,m)


def test_nested_assembly_without_direction_metadata_is_not_silently_flattened(tmp_path):
    m=Model()
    for i in (10,20,30):m.representation(i)
    m.sphere(30);m.edge(10,20,matrix((100,0,0)),matrix(),metadata=False)
    m.edge(20,30,matrix(),matrix(),metadata=False)
    with pytest.raises(ValueError,match='hierarchie'):run(tmp_path,m)


def test_cyclic_and_disconnected_assemblies_are_rejected(tmp_path):
    m=Model()
    for i in (10,20):m.representation(i)
    m.sphere(20);m.edge(10,20,matrix(),matrix());m.edge(20,10,matrix(),matrix())
    with pytest.raises(ValueError,match='zyklische'):run(tmp_path,m)


def test_product_metadata_disambiguates_parent_with_its_own_spherical_geometry(tmp_path):
    m=Model();m.representation(10,'holder');m.representation(20,'fiducial')
    m.sphere(10);m.sphere(20);m.edge(10,20,matrix((10,0,0)),matrix(),fusion=True)
    result=run(tmp_path,m)
    assert len(result.spheres)==1 and np.allclose(result.spheres[0].position_mm,[11,2,3])


def test_unassigned_coordinate_frames_are_not_guessed(tmp_path):
    m=Model();m.representation(10);m.representation(20);m.sphere(20)
    m.edge(10,20,matrix(),matrix());m.references[20]=()
    with pytest.raises(ValueError,match='Bezugssysteme'):run(tmp_path,m)


def test_invalid_spherical_coordinates_are_rejected(tmp_path):
    m=Model();m.representation(10);m.representation(20);m.sphere(20,(np.nan,0,0))
    m.edge(10,20,matrix(),matrix())
    with pytest.raises(ValueError,match='Kugelkoordinaten'):run(tmp_path,m)
