import re
directly_usable = set([ "int", "char", "float", "double", "long", "short", "size_t", "ssize_t", "bool" ])
fixed_regex = re.compile("^(u)?int[0-9]+_t$")
ll_regex = re.compile("^long\\s+long$")
ld_regex = re.compile("^long\\s+double$")
signed_char_regex = re.compile("^(un)?signed\\s+char$")
char_p_regex = re.compile("^char\\s*\*$")
void_p_regex = re.compile("^void\\s*\*$")
const_regex = re.compile("^const\\s+")
signed_regex = re.compile("^signed\\s+")
unsigned_regex = re.compile("^unsigned\\s+")

def convert_base_type(name):
    if fixed_regex.match(name):
        return "ct.c_" + name[:-2]
    elif name in directly_usable:
        return "ct.c_" + name
    elif ll_regex.match(name):
        return "ct.c_longlong"
    elif ld_regex.match(name):
        return "ct.c_longdouble"

    # '(un)signed char' have different names.
    elif signed_char_regex.match(name):
        if name.startswith("unsigned"):
            return "ct.c_ubyte"
        elif name.startswith("signed"):
            return "ct.c_byte"

    # define pointer offsets.
    elif void_p_regex.match(name) or name == "uintptr_t" or name == "intptr_t":
        return "ct.c_void_p"

    # Other signed/unsigned versions of non-pointer types.
    elif signed_regex.match(name):
        return convert_base_type(name[7:].lstrip())
    elif unsigned_regex.match(name):
        return convert_base_type(name[9:].lstrip())

    raise ValueError("don't yet know how to deal with type '" + name + "'")

unsupported_pointer_bases = set([ "uintptr_t", "intptr_t", "void" ])

def map_cpp_type(x):
    if x.pointer_level:
        if "void_p" in x.tags or "numpy" in x.tags:
            return "ct.c_void_p"

        pl = x.pointer_level
        core = ""

        if x.base_type in unsupported_pointer_bases:
            pl -= 1
            core = "ct.c_void_p"

        elif x.base_type == "char":
            pl -= 1
            core = "ct.c_char_p"

        else:
            try: 
                core = convert_base_type(x.base_type)
            except Exception as exc:
                raise ValueError("failed to parse type '" + x.full_type + "'") from exc

        for i in range(pl):
            core = "ct.POINTER(" + core + ")"
        return core
        
    else:
        try: 
            return convert_base_type(x.base_type)
        except Exception as exc:
            raise ValueError("failed to parse type '" + x.full_type + "'") from exc

def create_py_bindings(
    all_functions : dict, 
    output_path: str, 
    dll_prefix: str
):
    """Create the Python bindings for exported functions.

    Args:
        all_functions (dict): Dictionary as produced by `parse_cpp_exports`.
        output_path (str): Path to store the output Python bindings.
        dll_prefix (str): Prefix of the DLL for the compiled C++ code. 

    Returns:
        A file is created at `output_path`. Nothing is returned.
    """

    all_function_names = list(all_functions.keys())
    all_function_names.sort()

    with_numpy = False
    for x in all_function_names:
        restype, args = all_functions[x]
        for y in args:
            if "numpy" in y.type.tags:
                with_numpy = True
                break

    with open(output_path, "w") as handle:
        handle.write("""# DO NOT MODIFY: this is automatically generated by the ctypes-wrapper

import os
import ctypes as ct

def catch_errors(f):
    def wrapper(*args):
        errcode = ct.c_int32(0)
        errmsg = ct.c_char_p(0)
        output = f(*args, ct.byref(errcode), ct.byref(errmsg))
        if errcode.value != 0:
            msg = errmsg.value.decode('ascii')
            lib.free_error_message(errmsg)
            raise RuntimeError(msg)
        return output
    return wrapper

# TODO: surely there's a better way than whatever this is.
dirname = os.path.dirname(os.path.abspath(__file__))
contents = os.listdir(dirname)
lib = None
for x in contents:
    if x.startswith('""" + dll_prefix + """') and not x.endswith("py"):
        lib = ct.CDLL(os.path.join(dirname, x))
        break

if lib is None:
    raise ImportError("failed to find the """ + dll_prefix + """.* module")

lib.free_error_message.argtypes = [ ct.POINTER(ct.c_char_p) ]""")

        if with_numpy:
            handle.write("""

import numpy as np
def np2ct(x, expected, contiguous=True):
    if not isinstance(x, np.ndarray):
        raise ValueError('expected a NumPy array')
    if x.dtype != expected:
        raise ValueError('expected a NumPy array of type ' + str(expected) + ', got ' + str(x.dtype))
    if contiguous:
        if not x.flags.c_contiguous and not x.flags.f_contiguous:
            raise ValueError('only contiguous NumPy arrays are supported')
    return x.ctypes.data""")

        for k in all_function_names:
            restype, args = all_functions[k]
            if restype.base_type == "void" and restype.pointer_level == 0:
                handle.write("\n\nlib.py_" + k + ".restype = None\n")
            else:
                formatted_restype = None
                try:
                    formatted_restype = map_cpp_type(restype)
                except Exception as exc:
                    raise ValueError("failed to convert return value for function '" + k + "'") from exc
                handle.write("\n\nlib.py_" + k + ".restype = " + formatted_restype + "\n")

            argtypes = None
            try:
                argtypes = [map_cpp_type(x.type) for x in args]
            except Exception as exc:
                raise ValueError("failed to convert arguments for function '" + k + "'") from exc

            argtypes.append("ct.POINTER(ct.c_int32)")
            argtypes.append("ct.POINTER(ct.c_char_p)")
            handle.write("lib.py_" + k + ".argtypes = [\n    " + ",\n    ".join(argtypes) + "\n]")

        for k in all_function_names:
            restype, args = all_functions[k]
            argnames = [x.name for x in args]

            if with_numpy:
                argnames2 = []
                for x in args:
                    if with_numpy and "numpy" in x.type.tags:
                        args = ", np." 
                        if fixed_regex.match(x.type.base_type):
                            args += x.type.base_type[:-2]
                        else:
                            args += x.type.base_type
                        if "non_contig" in x.type.tags:
                            args += ", contiguous=False"
                        argnames2.append("np2ct(" + x.name + args + ")")
                    else:
                        argnames2.append(x.name)
            else:
                argnames2 = argnames

            handle.write("\n\ndef " + k + "(" + ", ".join(argnames) + """):
    return catch_errors(lib.py_""" + k + ")(" + ", ".join(argnames2) + """)""")

    return

