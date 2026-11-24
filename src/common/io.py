"""Shared backend for GridX NetCDF I/O."""


# Bypass the reproduced netCDF4 repeated-open string crash (upstream #1444).
NETCDF_ENGINE = "h5netcdf"
