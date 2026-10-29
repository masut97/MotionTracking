"""Synthetic STEP -> frozen converter UI, without saving any marker geometry."""
import hashlib
from pathlib import Path

import numpy as np


def fixture(fusion):
    entities=["#1=DIRECTION('',(0.,0.,1.));", "#2=DIRECTION('',(1.,0.,0.));",
              "#3=CARTESIAN_POINT('',(5.,0.,0.));", "#4=AXIS2_PLACEMENT_3D('',#3,#1,#2);",
              "#5=CARTESIAN_POINT('',(1.,2.,3.));", "#6=AXIS2_PLACEMENT_3D('',#5,#1,#2);",
              "#7=SPHERICAL_SURFACE('',#6,3.2);",
              "#10=SHAPE_REPRESENTATION('fiducial',(#4,#7),#900);",
              "#20=SHAPE_REPRESENTATION('root',(#40,#41,#42),#900);",
              "#900=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));"]
    for i,point in enumerate(((10,0,0),(0,20,0),(0,0,30))):
        axis,origin,transform,relation=40+i,50+i,60+i,70+i
        entities.extend([f"#{origin}=CARTESIAN_POINT('',{point});",
                         f"#{axis}=AXIS2_PLACEMENT_3D('',#{origin},#1,#2);"])
        first,second=(4,axis) if fusion else (axis,4)
        left,right=(10,20) if fusion else (20,10)
        entities.extend([f"#{transform}=ITEM_DEFINED_TRANSFORMATION('','',#{first},#{second});",
            f"#{relation}=(REPRESENTATION_RELATIONSHIP('','',#{left},#{right}) "
            f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{transform}) SHAPE_REPRESENTATION_RELATIONSHIP());"])
    return 'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n'+'\n'.join(entities)+'\nENDSEC;\nEND-ISO-10303-21;\n'


def run(output,root,parent):
    from PySide6.QtCore import Qt
    from atracsys_tracker import step_geometry as api
    from atracsys_tracker.ui.step_converter_dialog import StepConverterDialog
    output=Path(output);output.mkdir()
    geometry=Path(root)/'geometries'
    def snapshot():
        return {p.relative_to(geometry).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in geometry.rglob('*') if p.is_file()}
    before=snapshot();expected=np.array([[6,2,3],[-4,22,3],[-4,2,33]])
    for fusion in (False,True):
        path=output/('synthetic_fusion.step' if fusion else 'synthetic_solidworks.step')
        path.write_text(fixture(fusion),encoding='utf-8')
        extraction=api.extract_sphere_centres(path)
        assert np.allclose([s.position_mm for s in extraction.spheres],expected)
        if not fusion:
            original=api.extract_sphere_centres.__wrapped__(path)
            assert np.allclose([s.position_mm for s in original.spheres],expected)
        dialog=StepConverterDialog(Path(root),parent=parent)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
        dialog.load_step(path)
        assert dialog.table.rowCount()==3 and dialog.project_save_button.isEnabled()
        assert np.allclose(dialog._selected_points(),expected)
        dialog.close()
    assert snapshot()==before
    return {'passed':True,'formats':['SolidWorks direction','Fusion direction'],
            'geometry_files_unchanged':True,'hardware_used':False}
