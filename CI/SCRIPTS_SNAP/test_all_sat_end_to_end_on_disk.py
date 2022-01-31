""" Script testing EOReader satellites in a push routine """
import logging
import os
import tempfile

import xarray as xr
from cloudpathlib import AnyPath
from sertit import files

from CI.SCRIPTS.scripts_utils import (
    CI_EOREADER_S3,
    READER,
    dask_env,
    get_ci_db_dir,
    get_db_dir,
    get_db_dir_on_disk,
    opt_path,
    reduce_verbosity,
)
from eoreader.bands import *
from eoreader.env_vars import DEM_PATH, S3_DB_URL_ROOT, SAR_DEF_RES, TEST_USING_S3_DB
from eoreader.keywords import SLSTR_RAD_ADJUST
from eoreader.products import SlstrRadAdjust
from eoreader.products.product import Product, SensorType
from eoreader.reader import CheckMethod
from eoreader.utils import EOREADER_NAME

reduce_verbosity()

LOGGER = logging.getLogger(EOREADER_NAME)

MERIT_DEM_SUB_DIR_PATH = [
    "GLOBAL",
    "MERIT_Hydrologically_Adjusted_Elevations",
    "MERIT_DEM.vrt",
]


def set_dem(dem_path):
    """ Set DEM"""
    if dem_path:
        dem_path = AnyPath(dem_path)
        if not dem_path.is_file():
            raise FileNotFoundError(f"Not existing DEM: {dem_path}")
        os.environ[DEM_PATH] = str(dem_path)
    else:
        if os.environ.get(TEST_USING_S3_DB) not in ("Y", "YES", "TRUE", "T", "1"):
            try:
                merit_dem = get_db_dir().joinpath(*MERIT_DEM_SUB_DIR_PATH)
                # eudem_path = os.path.join(utils.get_db_dir(), 'GLOBAL', "EUDEM_v2", "eudem_wgs84.tif")
                os.environ[DEM_PATH] = str(merit_dem)
            except NotADirectoryError as ex:
                LOGGER.debug("Non available default DEM: %s", ex)
                pass
        else:
            if S3_DB_URL_ROOT not in os.environ:
                raise Exception(
                    f"You must specify the S3 db root using env variable {S3_DB_URL_ROOT} if you activate S3_DB"
                )
            merit_dem = "/".join(
                [os.environ.get(S3_DB_URL_ROOT), *MERIT_DEM_SUB_DIR_PATH]
            )
            os.environ[DEM_PATH] = merit_dem
            LOGGER.info(
                f"Using DEM provided through Unistra S3 ({os.environ[DEM_PATH]})"
            )


def _test_core_optical(pattern: str, dem_path=None, debug=False, **kwargs):
    """
    Core function testing optical data
    Args:
        pattern (str): Pattern of the satellite
        debug (bool): Debug option
    """
    possible_bands = [RED, SWIR_2, HILLSHADE, CLOUDS]
    _test_core(pattern, opt_path(), possible_bands, dem_path, debug, **kwargs)


def _test_core_sar(pattern: str, dem_path=None, debug=False, **kwargs):
    """
    Core function testing SAR data
    Args:
        pattern (str): Pattern of the satellite
        debug (bool): Debug option
    """
    possible_bands = [VV, VV_DSPK, HH, HH_DSPK, SLOPE]
    _test_core(
        pattern,
        get_ci_db_dir().joinpath("all_sar"),
        possible_bands,
        dem_path,
        debug,
        **kwargs,
    )


def _test_core(
    pattern: str,
    prod_dir: str,
    possible_bands: list,
    dem_path=None,
    debug=False,
    **kwargs,
):
    """
    Core function testing all data
    Args:
        pattern (str): Pattern of the satellite
        prod_dir (str): Product directory
        possible_bands(list): Possible bands
        debug (bool): Debug option
    """
    # Set DEM
    set_dem(dem_path)

    with xr.set_options(warn_for_unclosed_files=debug):

        # DATA paths
        pattern_paths = files.get_file_in_dir(
            prod_dir, pattern, exact_name=True, get_list=True
        )

        for path in pattern_paths:
            # WORKAROUND
            if str(path).endswith("/"):
                path = AnyPath(str(path)[:-1])

            LOGGER.info(
                "%s on drive %s (CI_EOREADER_S3: %s)",
                path.name,
                path.drive,
                os.getenv(CI_EOREADER_S3),
            )

            # Open product and set output
            LOGGER.info("Checking opening solutions")
            LOGGER.info("MTD")
            prod: Product = READER.open(path, method=CheckMethod.MTD, remove_tmp=False)

            # Log name
            assert prod is not None
            assert prod.name is not None
            LOGGER.info(f"Product name: {prod.name}")

            with tempfile.TemporaryDirectory() as tmp_dir:
                # output = os.path.join(
                #     "/mnt", "ds2_db3", "CI", "eoreader", "DATA", "OUTPUT_ON_DISK_CLEAN"
                # )
                output = tmp_dir
                is_zip = "_ZIP" if prod.is_archived else ""
                prod.output = os.path.join(output, f"{prod.condensed_name}{is_zip}")

                # Manage S3 resolution to speed up processes
                if prod.sensor_type == SensorType.SAR:
                    res = 1000.0
                    os.environ[SAR_DEF_RES] = str(res)
                else:
                    res = prod.resolution * 50

                # BAND TESTS
                LOGGER.info("Checking load and stack")
                stack_bands = [band for band in possible_bands if prod.has_band(band)]
                first_band = stack_bands[0]

                # Geometric data
                footprint = prod.footprint  # noqa
                extent = prod.extent  # noqa

                # Get stack bands
                # Stack data
                curr_path = os.path.join(tmp_dir, f"{prod.condensed_name}_stack.tif")
                stack = prod.stack(
                    stack_bands,
                    resolution=res,
                    stack_path=curr_path,
                    clean_optical="clean",
                    **kwargs,
                )

                # Load a band with the size option
                LOGGER.info("Checking load with size keyword")
                band_arr = prod.load(  # noqa
                    first_band,
                    size=(stack.rio.width, stack.rio.height),
                    clean_optical="clean",
                    **kwargs,
                )[first_band]
            prod.clear()


@dask_env
def test_s2():
    """Function testing the support of Sentinel-2 sensor"""
    _test_core_optical("*S2*_MSI*T30*")


@dask_env
def test_s2_theia():
    """Function testing the support of Sentinel-2 Theia sensor"""
    _test_core_optical("*SENTINEL2*")


@dask_env
def test_s3_olci():
    """Function testing the support of Sentinel-3 OLCI sensor"""
    # Init logger
    _test_core_optical("*S3*_OL_1_*")


@dask_env
def test_s3_slstr():
    """Function testing the support of Sentinel-3 SLSTR sensor"""
    # Init logger
    _test_core_optical("*S3*_SL_1_*", **{SLSTR_RAD_ADJUST: SlstrRadAdjust.SNAP})


@dask_env
def test_l8():
    """Function testing the support of Landsat-8 sensor"""
    # Init logger
    _test_core_optical("*LC08*")


@dask_env
def test_pla():
    """Function testing the support of PlanetScope sensor"""
    _test_core_optical("*202*1014*")


@dask_env
def test_pld():
    """Function testing the support of Pleiades sensor"""
    _test_core_optical("*IMG_PHR*")


@dask_env
def test_spot6():
    """Function testing the support of SPOT-6 sensor"""
    _test_core_optical("*IMG_SPOT6*")


@dask_env
def test_spot7():
    """Function testing the support of SPOT-7 sensor"""
    # This test orthorectifies DIMAP data, so we need a DEM stored on disk
    dem_path = os.path.join(get_db_dir_on_disk(), *MERIT_DEM_SUB_DIR_PATH)
    _test_core_optical("*IMG_SPOT7*", dem_path=dem_path)


@dask_env
def test_wv02_wv03():
    """Function testing the support of WorldView-2/3 sensors"""
    # This test orthorectifies DIMAP data, so we need a DEM stored on disk
    dem_path = os.path.join(get_db_dir_on_disk(), *MERIT_DEM_SUB_DIR_PATH)
    _test_core_optical("*P001_MUL*", dem_path=dem_path)


@dask_env
def test_ge01_wv04():
    """Function testing the support of GeoEye-1/WorldView-4 sensors"""
    _test_core_optical("*P001_PSH*")


@dask_env
def test_s1():
    """Function testing the support of Sentinel-1 sensor"""
    _test_core_sar("*S1*_IW*")


@dask_env
def test_s1_zip():
    """Function testing the support of Sentinel-1 sensor"""
    _test_core_sar("*S1*_IW*.zip")


@dask_env
def test_csk():
    """Function testing the support of COSMO-Skymed sensor"""
    _test_core_sar("*CSK*")


@dask_env
def test_csg():
    """Function testing the support of COSMO-Skymed 2nd Generation sensor"""
    _test_core_sar("*CSG*")


@dask_env
def test_tsx():
    """Function testing the support of TerraSAR-X sensor"""
    _test_core_sar("*TSX*")


# Assume that tests TDX and PAZ sensors
@dask_env
def test_tdx():
    """Function testing the support of TanDEM-X sensor"""
    _test_core_sar("*TDX*")


# Assume that tests TDX and PAZ sensors
@dask_env
def test_paz():
    """Function testing the support of PAZ SAR sensor"""
    _test_core_sar("*PAZ*")


@dask_env
def test_rs2():
    """Function testing the support of RADARSAT-2 sensor"""
    _test_core_sar("*RS2_*")


@dask_env
def test_rcm():
    """Function testing the support of RADARSAT-Constellation sensor"""
    _test_core_sar("*RCM*")


@dask_env
def test_iceye():
    """Function testing the support of ICEYE sensor"""
    _test_core_sar("*SLH_*")


# TODO:
# check non existing bands
# check cloud results


def test_invalid():
    wrong_path = "dzfdzef"
    assert READER.open(wrong_path) is None
    assert not READER.valid_name(wrong_path, "S2")
