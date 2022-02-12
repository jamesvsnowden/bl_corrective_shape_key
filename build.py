
from zipfile import ZipFile, ZIP_DEFLATED
from os.path import join

if __name__ == "__main__":
    zipobj = ZipFile('corrective_shape_key.zip', 'w', ZIP_DEFLATED)
    zipobj.write(join("lib", "curve_mapping", "__init__.py"), arcname=join("corrective_shape_key", "lib", "curve_mapping.py"))
    zipobj.write(join("lib", "transform_utils", "__init__.py"), arcname=join("corrective_shape_key", "lib", "transform_utils.py"))
    zipobj.write("__init__.py", arcname=join("corrective_shape_key", "__init__.py"))
    zipobj.close()
