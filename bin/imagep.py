#!/usr/bin/env python

"""
Insert description here.

"""
__version__ = '1.2.0'
__author__ = 'Michael Stadler'


import numpy as np
import os
from os import listdir
from os.path import isfile, join
import re
from skimage import filters, io
from ipywidgets import interact, IntSlider, Dropdown, IntRangeSlider, fixed
import matplotlib.pyplot as plt
from scipy import ndimage as ndi
from functools import partial
import skimage as ski
from skimage.filters.thresholding import threshold_li, threshold_otsu
from skimage.segmentation import flood_fill, watershed
from scipy.stats import mode
from skimage.measure import label, regionprops
from scipy.spatial import distance
# Bug in skimage: skimage doesn't bring modules with it in some environments.
# Importing directly from submodules (above) gets around this.

# Import my packages.
import sys
sys.path.append('/Users/MStadler/Bioinformatics/Projects/Zelda/Quarantine_analysis/bin')
from fitting import fitgaussian3d
############################################################################
# General image processing functions
############################################################################

def labelmask_filter_objsize(labelmask, size_min, size_max):
    """Filter objects in a labelmask by size.

    Args:
        labelmask: ndarray
            Integer labelmask
        size_min: int
            Minimum size, in pixels, of objects to retain
        size_max: int
            Maximum size, in pixels, of objects to retain

    Returns:
        labelmask_filtered: ndarray
            Input labelmask with all pixels not corresponding to objects
            within size range set to 0.
    """
    # Count pixels in each object.
    (labels, counts) = np.unique(labelmask, return_counts=True)
    # Select and keep only objects within size range.
    labels_selected = labels[(counts >= size_min) & (counts <= size_max)]
    labelmask_filtered = np.where(np.isin(labelmask, labels_selected), labelmask, 0)
    return labelmask_filtered

############################################################################
def imfill(mask, seed_pt='default'):
    '''Fill holes within objects in a binary mask.
    
    Equivalent to matlab's imfill function. seed_pt needs to be a point in 
    the "background" area. All 0 or False pixels directly contiguous with 
    the seed are defined as background, all other pixels are declared foreground.
    Thus any "holes" (0 pixels that are not contiguous with background) are 
    filled in. Conceptually, this is like using the "fill" function in classic
    paint programs to fill the background, and taking all non-background as 
    foreground.
    
    Args:
        mask: ndarray
            Binary mask of n dimensions.
        seed_pt: tuple
            Pixel in mask to use for seeding "background". This is the equi-
            valent of the point you click when filling in paint. Default will
            start at the all top left ([0,0], [0,0,0], etc.) and select the
            first zero pixel on the top line from the left.
    
    Returns:
        mask_filled: ndarray
            Binary mask filled    
    '''
    # Find a zero pixel for default start in upper left corner. Begins at 0,0
    # and walks to the right until it finds a zero.
    def find_zero(mask):
        # Grab top line of first Z-slice.
        topline = mask[tuple(np.zeros(mask.ndim - 1).astype('int'))]
        # Find the first zero pixel from left.
        first_zero = np.where(topline == 0)[0][0]
        # Return this location as seed.
        seed_pt = tuple(np.zeros(mask.ndim - 1).astype(int)) + tuple([first_zero])
        return seed_pt
    # By default, start in upper left corner.
    if (seed_pt == 'default'):
        #seed_pt = tuple(np.zeros(mask.ndim).astype(int))
        seed_pt = find_zero(mask)
    # Fill all background pixels by changing them to 1. Changes are made to
    # original mask, so 1s are carried over in mask_flooded.
    mask_flooded = flood_fill(mask, seed_pt,1)
    # Identify background pixels by those that are changed from original mask.
    # Unchanged pixels (0s and 1s) in original mask are now "filled" foreground.
    mask_filled = np.where(mask == mask_flooded, 1, 0)
    return mask_filled

############################################################################
def local_max(img, size=(70,100,100)):
    """Find local maxima pixels in an image stack within a given window.
    
    Defines local maxima as pixels whose value is equal to the maximum value
    within a defined window centered on that point. Implementation is to
    first run a maximum filter, then define pixels in the original image 
    whose value is equal to the max-filter result at the same positions as
    local maximum pixels. Returns a binary mask of such pixels.
    
    Args:
        img: ndarray
            Image stack
        size: tuple of ints
            Size of the window for finding local maxima. The sizes are the
            dimensions of the filter used to search for maxima. So a size
            of (100, 100) will use a square with side lengths of 100 pixels.
            Generally, you want the size dimensions to match the dimensions
            of the objects you're searching for.
    
    Returns:
        local_max: ndarray
            A binary mask with dimensions equal to img of pixels whose value 
            is equal to the local maximum value. 
    """
    # Apply a maximum filter.
    max_f = ndi.maximum_filter(img, size=size)
    # Find pixels that are local maxima.
    local_max = np.where(max_f == img, 1, 0)
    return(local_max)

############################################################################
def peak_local_max_nD(img, size=(70,100,100), min_dist=0):
    """Find local maxima in an N-dimensional image.
    
    Generalizes scikit's peak_local_max function to three (or more) 
    dimensions. Finds local maxima pixels within supplied window, determines
    centroids for connected pixels, and returns a mask of these centroid
    positions and a list of them.
    
    Suggested usage: finding seed points for watershed segmentation from 
    distance transform. It is necessary because there are often multiple
    pixels with the same distance value, leaving little clusters of connected
    pixels. For circular objects (nuclei), distance transforms will form
    nice connected local max clusters. For less circular nuclei/objects,
    sometimes multiple unconnected clusters occur within an object. This
    is the reason for adding the minimum distance function.
    
    Args:
        img: ndarray
            N-dimensional image stack
        size: tuple of ints
            Size of the window for finding local maxima. The sizes are the
            dimensions of the filter used to search for maxima. So a size
            of (100, 100) will use a square with side lengths of 100 pixels.
            Generally, you want the size dimensions to match the dimensions
            of the objects you're searching for.
        min_dist: numeric
            Minimum (euclidean) distance in pixels allowed between peaks. If
            two peaks are within the minimum distance, the numerically lower
            peak (arbitrary) wins. 
    
    Returns:
        tuple: (local_peak_mask, local_peaks)
        local_peak_mask: ndarray
            A labelmask with dimensions equal to img of single labeled 
            pixels representing local maxima.
        local_peaks: list of tuples
            Coordinates of pixels masked in local_peak_mask  
    """
    def has_neighbor(peak, peak_list, min_dist):
        """Find whether a peak already exists within minimum distance of this peak"""
        for testpeak in peak_list:
            if (distance.euclidean(peak, testpeak) < min_dist):
                return True
        return False
    # Find pixels that represent local maxima. Produces clusters of connected
    # pixels at the centers of objects.
    maxes = local_max(img, size)
    # Connect these pixels in a labelmask.
    conn_comp, info = ndi.label(maxes)
    # Get the centroids of each local max object, update mask and list.
    local_peak_mask = np.zeros_like(img)
    local_peaks = []
    peak_num=1
    for id_ in np.unique(conn_comp)[1:]:
        centroid = get_object_centroid(conn_comp, id_)
        # If there is no already-added seed within the minimum distance,
        # add this seed to the mask and list.
        if (not has_neighbor(centroid, local_peaks, min_dist)):
            local_peak_mask[centroid] = peak_num
            local_peaks.append(centroid)
            peak_num = peak_num + 1
    return local_peak_mask, local_peaks

############################################################################
def get_object_centroid(labelmask, id):
    """Find the centroid of an object in a labelmask.
    
    Args:
        labelmask: ndarray
            Labelmask of arbitrary dimensions
        id: int
            Label of object to find centroid for
            
    Returns:
        centroid: tuple of ints
            Coordinates of the object's centroid
    """
    # Get coordinates 
    coords = np.where(labelmask == id)
    # Find mean of each coordinate, remove negatives, make int.
    return tuple([int(np.mean(x)) for x in coords])

############################################################################
def get_object_centroid(labelmask, id):
    """Find the centroid of an object in a labelmask.
    
    Args:
        labelmask: ndarray
            Labelmask of arbitrary dimensions
        id: int
            Label of object to find centroid for
            
    Returns:
        centroid: tuple of ints
            Coordinates of the object's centroid
    """
    # Get coordinates 
    coords = np.where(labelmask == id)
    # Find mean of each coordinate, remove negatives, make int.
    return tuple([int(np.mean(x)) for x in coords])

############################################################################
def labelmask_apply_morphology(labelmask, mfunc, struct=np.ones((2,2,2)), 
                               expand_size=(1,1,1), **kwargs):
    """Apply morphological functions to a labelmask.
    
    Args:
        labelmask: ndarray
            N-dimensional integer labelmask
        mfunc: python function
            Function from scikit.ndimage.morphology module
        struct: ndarray
            Structuring element for morpholocial operation
        expand_size: tuple of ints
            Size in N dimensions for maximum filter to produce label lookup
            mask (see details below)
        **kwargs: keyword arguments
            Keyword arguments supplied to morphological function
            
    Returns:
        new_labelmask: ndarray
            Updated labelmask matching shape of labelmask
    
    This is an imperfect solution to applying morphological operations to 
    labelmasks, so one needs to be a bit careful. The basic strategy is to
    binarize the mask, perform operations on it, and then from that mask 
    look up labels in the previous mask. This is better than re-labeling 
    because it allows the operations to produce objects that touch without 
    merging them. The issue is looking up the labels, which seems to be non-
    trivial. 
    
    The solution here is to generate a "lookup mask" by applying a maximum 
    filter (size determined by expand_size) to the labelmask, which expands 
    each object into its local area. As long as resulting morphological 
    operations keep the object within this area, they'll get the proper label. 
    As long as objects in the original image are spaced farther than the 
    supplied sizes in the three dimensions, this will work perfectly well. If 
    this isn't true, the object with the numerically larger label (arbitrary) 
    will expand at the expense of its neighbor. Of note, this maximum filter is 
    mathematially identical to morpholocial dilation when their is non conflict 
    between objects.
    
    For labelmasks with well-spaced objects, the function works as expected. 
    For closely spaced objects, one needs to select an expand_size that will
    generally be less than or equal to the object separation. For most
    applications, conflicts at edges won't be of great consequence but should
    be kept in mind.
    
    Suggested settings:
    
        -For operations that reduce the size of objects, leave expand_size 
        at (1,1,1), as resulting objects will be entirely contained within
        original objects.
        
        -For operations that increase the size of objects, set expand_size 
        to be equal to or slightly greater than expected increases in object
        size, and no more than the typical separation between objects.
        
    Examples:
    
    Dilation: 
        labelmask_apply_morphology(mylabelmask, ndi.morphology.binary_dilation,
        struct=np.ones((1,7,7)), expand_size=(1,8,8))
    
    Erosion:
       labelmask_apply_morphology(mylabelmask, ndi.morphology.binary_erosion,
        struct=np.ones((1,7,7))) 
        
    
    """
    # Expand the objects in the original mask to provide a "lookup" mask for
    # matching new objects to labels.
    lookupmask = ndi.maximum_filter(labelmask, expand_size)
    
    # Perform morphological operation on binarized labelmask.
    new_binmask = mfunc(labelmask, struct, **kwargs)
    
    # Match labels in new mask to those of lookup mask.
    new_labelmask = np.where(new_binmask, lookupmask, 0)
    return new_labelmask

############################################################################
def mesh_like(arr, n):
    """Make mesh grid for last n dimensions of an array
    
    Makes a meshgrid with the same shape as the last n dimensions of input
    array-like object.
    
    Args:
        arr: array-like
            Array-like object that has a shape parameter
        n: int
            Number of dimensions, from the right, for which to make meshgrid.
    
    Returns:
        meshes: list of ndarrays
            Each element of list corresponds to ordered dimension of input,
            ndarrays are corresponding meshgrids of same shape as arr.
    """
    if (n > arr.ndim):
        raise ValueError('n is larger than the dimension of the array')
    # Make vectors of linear ranges for each dimension.
    vectors = []
    for i in reversed(range(1, n+1)):
        a = np.arange(0, arr.shape[-i])
        vectors.append(list(a))
    # Make meshgrids from vectors.
    meshes = np.meshgrid(*vectors, sparse=False, indexing='ij')
    return meshes

############################################################################
def find_background_point(mask):
    """Find a background (0) pixel in a mask.
    
    Searches for a background pixel in a mask, defined as a pixel with 
    value 0.

    Args:
        mask: ndarray
            Mask of abritrary dimensions, background must be 0.

    Returns: 
        coord: tuple of ints
            Coordinates of a single background pixel.
    """
    zerocoords = np.where(mask == 0)
    coord = zerocoords[0][0]
    for n in range(1, len(zerocoords)):
        coord = np.append(coord, zerocoords[n][0])
    return tuple(coord)  

############################################################################
def relabel_labelmask(labelmask):
    """Relabel labelmask to set background to 0 and object IDs to be linearly 
    ascending from 1
    
    Args:
        labelmask: ndarray
            N-dimensional labelmask.
    
    Returns:
        labelmask: ndarray
            Labelmask of same shape as input, with the largest object's (
            background) ID set to 0 and all other objects labeled 1..n
    
    """
    mask = np.copy(labelmask)
    # Get all object labels and their counts.
    labels, counts = np.unique(mask, return_counts=True)
    # Get the indexes of sorted counts, descending.
    ordered_indexes = np.argsort(counts)[::-1]
    # Set largest object as background (ID=0).
    background_label = labels[ordered_indexes[0]]
    mask[mask == background_label] = 0
    # Renumber the rest of the objects 1..n.
    obj_num=1
    for n in ordered_indexes[1:]:
        old_label = labels[n]
        mask[labelmask == old_label] = obj_num
        obj_num = obj_num + 1
    return mask
############################################################################
# Function implementing filters
############################################################################

def gradient_nD(stack):
    """Find the gradient of an n-dimensional image.
    
    Approximates an nD (typically: 3D) gradient by applying a gradient filter
    separately on each axis and taking the root of the sum of their squares.

    Args:
        stack: ndarray
            Image stack in [z, x, y] or [x, y]
            
    Returns:
        gradient: ndarray
            Gradient transform of image in same shape as stack
    """
    # Convert for 64-bit to avoid large number problems in squares.
    stack = np.copy(stack)
    stack = stack.astype(np.float64)
    sumsq = ndi.filters.sobel(stack, axis=0) ** 2
    for d in range(1, stack.ndim):
         sumsq = sumsq + (ndi.filters.sobel(stack, axis=d) ** 2)
    gradient = np.sqrt(sumsq)
    return gradient

############################################################################
def dog_filter(stack, sigma_big, sigma_small):
    """Difference of Gaussians filter
    
    Args:
        stack: ndarray
            n-dimensional image stack
        sigma_big: int
            Larger sigma for gaussian filter
        sigma_small: int
            Smaller sigma for gaussian filter
    
    Returns:
        dog: ndarray
            DoG filtered stack of same shape as input stack.
    """
    stack_cp = stack.astype(np.int16)
    return ndi.filters.gaussian_filter(stack_cp, sigma=sigma_big) - ndi.filters.gaussian_filter(stack_cp, sigma=sigma_small)

############################################################################
def log_filter(stack, sigma):
    """Laplacian of Gaussian filter
    
    Args:
        stack: ndarray
            n-dimensional image stack
        sigma: int
            Sigma for gaussian filter
    
    Returns:
        log: ndarray
            LoG filtered stack of same shape as input stack.
    """
    stack_cp = stack.astype(np.int16)
    gauss = ndi.filters.gaussian_filter(stack_cp, sigma=sigma)
    log = ndi.filters.laplace(gauss)
    return log

############################################################################
# Functions for loading TIFF stacks
############################################################################

# Main function for loading TIFF stacks
def _read_tiff_stack(tif_folder, tif_files, **kwargs):
    """Read a folder of 2D or 3D TIFF files into a numpy ndarray.
    
    Args:
        tif_folder: string
            Directory containing multiple TIFF files.
        tif_files: list
            List of files in the folder to load. Must be sorted in order
            desired.
        span: tuple of ints
            Optional key-word argument specifying first and last file to 
            load, both inclusive. Example: span=(0, 5) loads the first 6 
            images, numbers 0 through 5.
    
    Returns:
        stack: ndarray
            n-dimensional image stack with the new dimension (file number) 
            in the 0 position(file_num, z, x, y) for 3D stacks, (filenum, x,
            y) for 2D stacks
            
    Raises:
        ValueError: 
            If dimensions of TIFF file don't match those of the first
            file
    """
    if 'span' in kwargs:
        first, last = (kwargs['span'])
        if (first <= last) and (last < len(tif_files)):
            tif_files = tif_files[first:(last + 1)]
        else:
            raise ValueError('Span exceeds the dimensions of the stack')

    # Create stack with dimensions from first file.
    img = io.imread(join(tif_folder, tif_files[0]))
    dims = img.shape
    num_files = len(tif_files)
    stack = np.ndarray(((num_files,) + dims), dtype=img.dtype)
    stack[0] = img
    img_num = 1
    
    # Add the rest of the files to the stack.
    for tif_file in tif_files[1:]:
        # Add image data to ndarray
        img = io.imread(join(tif_folder, tif_file))
        # Check dimensions
        if not stack[0].shape == img.shape:
            raise ValueError(f'Dimensions do not match previous files: {tif_file}')
        stack[img_num] = img
        img_num = img_num + 1
        
    return stack

# Wrapper function for loading all TIFF files in a folder
def read_tiff_folder(tif_folder):
    """Read all TIFF files in a folder into an ndarray.
    
        Args:
            tif_folder: string
                Directory containing multiple TIFF files. Must be sortable
                asciibetically.
            span: tuple of ints
                Optional key-word argument specifying first and last file to 
                load, both inclusive. Example: span=(0, 5) loads the first 6 
                images, numbers 0 through 5.
        
        Returns:
            stack: ndarray
                n-dimensional image stack with the new dimension (file number) 
                in the 0 position(file_num, z, x, y) for 3D stacks, (filenum, 
                x, y) for 2D stacks
                
        Raises:
            ValueError: 
                If dimensions of TIFF file don't match those of the first
                file
    """
    
    # Compile files that are files and have .tif extension (case-insensitive).
    tif_files = [f for f in listdir(tif_folder) if (isfile(join(tif_folder, f)) 
        and (os.path.splitext(f)[1][0:4].upper() == '.TIF'))]
    # Sort the files: asciibetical sorting produces files ascending in time 
    # (sorting is *in place*)
    tif_files.sort()
    return _read_tiff_stack(tif_folder, tif_files)

# Wrapper function for loading TIFF files in a lattice-style folder, with
# CamA and CamB channels.
def read_tiff_lattice(tif_folder, **kwargs):
    """Read all TIFF files in a lattice output folder into an ndarray.
    
        Args:
            tif_folder: string
                Directory containing multiple TIFF files with 'CamA' and 'CamB' 
                Must be equal numbers of CamA and CamB and files must be 
                sortable asciibetically.
            span: tuple of ints
                Optional key-word argument specifying first and last file to 
                load, both inclusive. Example: span=(0, 5) loads the first 6 
                images, numbers 0 through 5.
        
        Returns:
            stack: ndarray
                n-dimensional image stack with the new dimension (channel) 
                in the 0 position, e.g. (channel, t, z, x, y) for 3D stacks. 
                
        Raises:
            ValueError: 
                If dimensions of TIFF file don't match those of the first file
            ValueError: 
                If there are non-identical numbers of CamA and CamB files
    """
    
    # Compile files that are files and have .tif extension (case-insensitive).
    tif_files = [f for f in listdir(tif_folder) if (isfile(join(tif_folder, f)) 
        and (os.path.splitext(f)[1][0:4].upper() == '.TIF'))]
    # Sort files into two lists based on containing 'CamA' and 'CamB' in filename.
    regex_camA = re.compile('CamA')
    regex_camB = re.compile('CamB')
    camA_files = [*filter(regex_camA.search, tif_files)] # This syntax unpacks filter into a list.
    camB_files = [*filter(regex_camB.search, tif_files)]

    # Sort the files: asciibetical sorting produces files ascending in time 
    # (sorting is *in place*)
    camA_files.sort()
    camB_files.sort()
    # Read both sets of files, combine if they are of same dimensions.
    camA_stack = _read_tiff_stack(tif_folder, camA_files, **kwargs)
    camB_stack = _read_tiff_stack(tif_folder, camB_files, **kwargs)
    if camA_stack.shape == camB_stack.shape:
        return np.stack((camA_stack, camB_stack), axis=0)
    else:
        raise ValueError('Unequal number of CamA and CamB files.')

############################################################################
# Functions for interactive image viewing/analysis
############################################################################

def viewer(stacks, order='tzxy', figsize=6):
    """Interactive Jupyter notebook viewer for n-dimensional image stacks.
    
    Args:
        stack: list of ndarrays
            List of n-dimensional image stacks; last two dimensions must 
            be x-y to display. Image shapes must be identical.
        order: string
            String specifying order of image dimensions. Examples: 'ctzxy' 
            or 'tzxy'. Last two dimensions must be 'xy'.
            
    Returns: none
        
    Raises:
        ValueError: 
            If final two dimensions are not xy
        ValueError:
            If the number of dimensions in the order string does not match
            the dimensions of the stack
    """
    # Update the displayed image with inputs from widgets.
    def _update_view(order, **kwargs):
        numplots = len(stacks)
        indexes = []
        colmap = kwargs['colmap']
        min_ = kwargs['contrast'][0]
        max_ = kwargs['contrast'][1]
        
        # Unpack order variable into array.
        order_arr = [char for char in order[:-2]]
        # Populate indexes list with widgets.
        for n in order_arr:
            indexes.append(kwargs[n])
        
        # Set up frame for plots.
        fig, ax = plt.subplots(1, numplots, figsize=(figsize * numplots, figsize * numplots))
        # If only one plot, pack ax into list
        if (type(ax) is not np.ndarray):
            ax = [ax]
        for n in range(0, numplots):
            stack_local = stacks[n]
            # Slice stack, leaving last two dimensions for image.
            # Note: the (...,) in the following is not required, but I think 
            # it is clarifying.
            ax[n].imshow(stack_local[tuple(indexes) + (...,)], cmap=colmap, vmin=min_, 
            vmax=max_);    
    
    # Make a new slider object for dimension selection and return it
    def _make_slider(n):
        widget = IntSlider(min=0, max=(stack.shape[n] - 1), step=1, 
            continuous_update=False,)
        return(widget)
    
    # Make a dropdown widget for selecting the colormap.
    def _make_cmap_dropdown():
        dropdown = Dropdown(
            options={'viridis', 'plasma', 'magma', 'inferno','cividis',
                'Greens', 'Reds', 'gray', 'gray_r', 'prism'},
            value='viridis',
            description='Color',
        )
        return dropdown
    
    # Make a range slider for adjusting image contrast.
    def _make_constrast_slider():
        min_ = stack.min()
        max_ = stack.max()
        contrast_slider = IntRangeSlider(
            value=[min_, max_],
            min=min_,
            max=max_,
            step=1,
            description='Contrast',
            disabled=False,
            continuous_update=False,
            orientation='horizontal',
            readout=True,
            readout_format='d',
        )
        return contrast_slider
    
    def main(order):
        if not (order[-2:] == 'xy'):
            raise ValueError("Dimension order must end in 'xy'.")
        if not (len(order) == len(stack.shape)):
            raise ValueError("Order string doesn't match dimensions of stack")
        # Split order string (minus trailing 'xy') into list.
        order_arr = [char for char in order[:-2]]
        
        interact_call = {} 
        #interact_call['order'] = order # passing arrays through interact no good. 
        interact_call['colmap'] = _make_cmap_dropdown()
        interact_call['contrast'] = _make_constrast_slider()
               
        # Build call to interact by appending widgets for all leading dimensions.   
        for n in range(0, len(order_arr)):
            func = _make_slider(n)
            interact_call[order_arr[n]] = func

        # Make color and contrast widgets.
        interact(_update_view, order=fixed(order), **interact_call)
    # Use first stack as reference for sizes, etc.
    if (type(stacks) is list):
        stack = stacks[0]
    else:
        stack = stacks
        stacks = [stack]
    main(order)

############################################################################
def qax(n, ncol=4):
    """Quick axes: generate 1d list of axes objects of specified number
    
    Args:
        n: int
            Number of plots desired
            
    Returns:
        ax1d: list
            1D list of axes objects in order top left to bottom right (columns
            then rows)
    """
    nrow = int(np.ceil(n / ncol))
    if (n < ncol):
        ncol = n
    fig, ax = plt.subplots(nrow, ncol, figsize=(16, 4*nrow))
    ax1d = []
    pos1d = 0
    if (nrow > 1):
        for r in range(0, nrow):
            for c in range(0, ncol):
                ax1d.append(ax[r][c])
                pos1d = pos1d + 1
    else:
        for c in range(0, ncol):
            ax1d.append(ax[c])
            pos1d = pos1d + 1
    
    return ax1d

############################################################################
def plot_ps(func, span=range(0,8)):
    """Plot a parameter series in a specified range
    
    User supplies a plotting function that takes a single integer input as
    a parameter. plot_ps builds axes to display all parameter values and
    serially calls plot function on them.
    
    Example:
       def temp(x):
            dog = dog_filter(red, x, 3)
            plt.imshow(dog)

        plot_ps(temp, range(8,13)) 
    
    Args:
        func: function
            Must take a single integer value as a parameter and call a plot
            function on the active axes object.
        span: range
            Range object containing values of parameter to plot. 
    """
    nplots = len(span)
    ax = qax(int(len(span)))
    for pln in range(0, len(span)):
        plt.sca(ax[pln])
        func(span[pln])

############################################################################
def box_spots(stack, spot_data, max_mult=1.3, halfwidth_xy=15, 
              halfwidth_z=8, linewidth=3, shadows=True):
    """Draw boxes around detected MS2 spots.
    
    Usage suggestions: Useful with a Z-projection to examine effectiveness
    of spot segmentation. Can also use with very small halfwidths to create
    artificial "dots" representing called spots to overlay on other data,
    e.g. a nuclear mask or a blank matrix of 0s (to examine spot movement
    alone).
    
    Args: 
        stack: ndarray of uint16
            Multi-dimensional image stack of dimensions [t,z,x,y]
        channel: int
            Channel containing MS2 spots
        spot_data: dict of ndarrays
            Data containing tracking of spots detected. Dict entries are unique 
            spot IDs (numeric 1...), rows of ndarray are detections of the spot 
            in a single frame. Time must be column 0, [z,x,y] in columns 2:4.
        max_multi: numeric
            Multiplier of maximum value to use for drawing box.
        halfwidth_xy: int
            Halfwidth in pixels of the boxes in xy direction (sides will be 
            2*halfwidth)
        halfwidth_z: int
            Halfwidth of the boxes in z direction(vertical sides will be 
            2*halfwidth)
        linewidth: int
            Width of lines used to draw boxes
        shadows: bool
            Draw "shadows" (dark boxes) in non-boxed z-slices.
        
    Return:
        boxstack: ndarray
            Selected channel of input image stack with boxes drawn around 
            spots. Dimensions [t,z,x,y]
    """
    if (stack.dtype != 'uint16'):
        raise ValueError("Stack must be uint16.")
    boxstack = np.copy(stack)
    hival = max_mult * boxstack.max()
    if (hival > 65535):
        hival = 65535
    
    def drawbox(boxstack, point, halfwidth_xy, halfwidth_z, linewidth, hival, shadows):
        t, z, i, j = point
        z_min = max(0, z - halfwidth_z)
        z_max = min(boxstack.shape[1], z + halfwidth_z + 1)
        i_min = max(0, i - halfwidth_xy)
        i_max = min(boxstack.shape[2], i + halfwidth_xy + 1)
        j_min = max(0, j - halfwidth_xy)
        j_max = min(boxstack.shape[3], j + halfwidth_xy + 1)
        if shadows:
            # Draw shadow boxes in all Z-frames.
            boxstack[t, :, i_min:i_max, j_min:(j_min + linewidth)] = 0
            boxstack[t, :, i_min:i_max, (j_max-linewidth):j_max] = 0
            boxstack[t, :, i_min:(i_min+linewidth), j_min:j_max] = 0
            boxstack[t, :, (i_max-linewidth):i_max, j_min:j_max] = 0
        # Draw left line.
        boxstack[t, z_min:z_max, i_min:i_max, j_min:(j_min + linewidth)] = hival     
        # Draw right line. 
        boxstack[t, z_min:z_max, i_min:i_max, (j_max-linewidth):j_max] = hival
        # Draw top line. 
        boxstack[t, z_min:z_max, i_min:(i_min+linewidth), j_min:j_max] = hival
        # Draw bottom line.
        boxstack[t, z_min:z_max, (i_max-linewidth):i_max, j_min:j_max] = hival
    
    # Main.
    for spot in spot_data:
        arr = spot_data[spot]
        for row in arr:
            row = row.astype(int)
            point = (row[[0,2,3,4]])
            drawbox(boxstack, point, halfwidth_xy, halfwidth_z, linewidth, hival, shadows)
    return boxstack   

############################################################################
# Functions for segmenting images
############################################################################

def segment_embryo(stack, channel=0, sigma=5, walkback = 50):
    """Segment the embryo from extra-embryo space in lattice data.
    
    Details: Crudely segments the embryo from extra-embryonic space in 5-
    dimensional stacks. The mean projection across all time slices is first
    taken, followed by performing a gaussian smoothing, then thresholding,
    then using morphological filtering to fill holes and then "walking
    back" from right-to-left, based on the observation that segmentation 
    tends to extend too far, and lattice images always have the sample on
    the left.
    
    Args:
        stack: ndarray
            Image stack in order [c, t, z, x, y]
        channel: int
            Channel to use for segmentation (channel definted as first
            dimension of the stack)
        sigma: int
            Sigma factor for gaussian smoothing
        walkback: int
            Length in pixels to "walk back" from right
            
    Returns:
        stack_masked: ndarray
            Input stack with masked (extra-embryo) positions set to 0
    """
    # Create a 3D mask from the mean projection of a 4D stack.
    def _make_mask(stack, channel, sigma, walkback):
        # Make a mean projection (on time axis) for desired channel. 
        im = stack[channel].mean(axis=0)
        # Smooth with gaussian kernel.
        im_smooth = ndi.filters.gaussian_filter(im, sigma=sigma)
        # Find threshold with minimum method.
        t = ski.filters.threshold_minimum(im_smooth)
        # Make binary mask with threshold.
        mask = np.where(im_smooth > t, im, 0)
        mask = mask.astype('bool')
        # Fill holes with morphological processing.
        mask = ndi.morphology.binary_fill_holes(mask, structure=np.ones((1,2,2)))
        # Build structure for "walking back" from right via morphological processing.
        struc = np.ones((1,1, walkback))
        midpoint = int(walkback / 2)
        struc[0, 0, 0:midpoint] = 0
        # Walk back mask from right.
        mask = ndi.morphology.binary_erosion(mask, structure=struc)
        return mask
    
    def main(stack, channel, sigma, walkback):
        mask = _make_mask(stack, channel, sigma, walkback)
        stack_masked = np.where(mask, stack, 0) # Broadcasting mask onto stack
        return(stack_masked)
    
    return main(stack, channel, sigma, walkback)

############################################################################
def labelmask_filter_objsize(labelmask, size_min, size_max):
    """Filter objects in a labelmask for size
    
    Args:
        labelmask: ndarray
            n-dimensional integer labelmask
        size_min: int
            Minimum size in total pixels, of the smallest object
        size_max: int
            Maximum size, in total pixels, of the largest object
    
    Return:
        labelmask_filtered: ndarray
            Labelmask of same shape as input mask, containing only objects
            between minimum and maximum sizes.
    """
    # Count pixels in each object.
    (labels, counts) = np.unique(labelmask, return_counts=True)
    # Select objects in desired size range, update filtered mask.
    labels_selected = labels[(counts >= size_min) & (counts <= size_max)]
    labelmask_filtered = np.where(np.isin(labelmask, labels_selected), 
        labelmask, 0)
    return labelmask_filtered

############################################################################
def object_circularity(labelmask, label):
    """Calculate circularity for and object in a labelmask
    
    Implements imageJ circularity measure: 4pi(Area)/(Perimeter^2).
    
    Args:
        labelmask: ndarray
            n-dimensional integer labelmask
        label: int
            ID of object for which to calculate circularity
            
    Return:
        circularity: float
            output of circularity calculation 
    """
    # Find z slice with most pixels from object.
    z, i, j = np.where(labelmask == label)
    zmax = mode(z)[0][0]
    # Select 2D image representing object's max Z-slice.
    im = np.where(labelmask[zmax] == label, 1, 0)
    # Calculate circularity from object perimeter and area.
    regions = regionprops(im)
    perimeter = regions[0].perimeter
    area = regions[0].area
    circularity = 4 * np.pi * area / (perimeter ** 2) 
    return circularity
    
############################################################################
def filter_labelmask(labelmask, func, above=0, below=1e6):
    """Filter objects from a labelmask based on object properties
    
    Applies a user-supplied function that returns a numeric value for an
    object in a labelmask, filters mask to contain only objects between
    minimum and maximum values.
    
    Args:
        labelmask: ndarray
            n-dimensional integer labelmask
        func: function
            Function that accepts a labelmask as its first argument, object
            ID as second argument, and returns a numeric value.
        above: numeric
            Lower limit for object's value returned from the function.
        below: numeric
            Upper limit for object's value returned from the function.
            
    Return:
        labelmask_filtered: ndarray
            Labelmask of same shape as input mask, containing only objects
            between minimum and maximum values from supplied function.
    """
    labels = []
    for x in np.unique(labelmask):
        prop = func(labelmask, x)
        if (prop >= above and prop <= below):
            labels.append(x)
    labelmask_filtered = np.where(np.isin(labelmask, labels), labelmask, 0)
    return labelmask_filtered

############################################################################
def zstack_normalize_mean(instack):
    """Normalize each Z-slice in a Z-stack to by dividing by its mean

    Args:
        instack: ndarray
            Image stack in order [z, x, y]

    Returns:
        stack: ndarray
            Image stack of same shape as instack.
    """
    stack = np.copy(instack)    
    stackmean = stack.mean()
    for x in range(0,stack.shape[0]):
        immean = stack[x].mean()
        stack[x] = stack[x] / immean * stackmean
    return(stack)

############################################################################
def stack_bgsub(stack, bgchannel=0, fgchannel=1):
    """Use one channel of image stack to background subtract a second channel.

    Built for 2-color lattice MS2 stacks. Observation is that low-frequency
    features in MS2 channel (typically red) are almost all shared background
    structures, particularly the embryo boundary. Subtraction is a very 
    effective method of removing this boundary and other non-specific signals.
    The mean projection in time of the background channel is used for
    subtraction.
    
    Args:
        stack: ndarray
            5D image stack of dimensions [c,t,z,x,y].
        bgchannel: int
            Channel to use for background (to be subtracted)
        fgchannel: int
            Channel to use for foreground (to be subtracted from)
    
    Returns:
        bgsub: ndarray
            Background-subtracted stack in same shape as input stack
    """
    # Generate background from mean projection in time.
    bg = stack[bgchannel].mean(axis=0)
    # Find scale factor to equalize mean intensities.
    scale = stack[fgchannel].mean() / bg.mean()
    # Subtract background (broadcast to whole array, in both channels)
    bgsub = stack - (scale * bg)
    # Set minimum value to 0 (remove negative values).
    bgsub = bgsub + abs(bgsub.min())
    return bgsub

############################################################################
def segment_nuclei3D_5(instack, sigma1=3, sigma_dog_small=5, sigma_dog_big=40, seed_window=(70,100,100),
                       erosion_length=5, dilation_length=10, sensitivity=0.5, size_min=1e4, 
                       size_max=5e5, circularity_min=0.5, display=False):
    """Segment nuclei from a 3D imaging stack
   
    Args:
        instack: ndarray
            3D image stack of dimensions [z, x, y].
        sigma1: int
            Sigma for initial Gaussian filter, for making initial mask
        sigma_dog_small: int
            Smaller sigma for DoG filter used as input to gradient for watershed
        sigma_dog_big: int
            Larger sigma for DoG filter used as input to gradient for watershed
        seed_window: tuple of three ints
            Size in [z, x, y] for window for determining local maxes in distance
            transform. Generally want size to be ~ size of nuclei.
        erosion_length: int
            Size in x and y of structuring element for erosion of initial mask.
        dilation_length: int
            Size in x and y of structuring element for dilating objects after
            final segmentation.
        size_min: int
            Minimum size, in pixels, of objects to retain
        size_max: int
            Maximum size, in pixels, of objects to retain
        circularity_min: float
            Minimum circularity measure of objects to retain
    
    Returns:
        labelmask: ndarray
            Mask of same shape as input stack with nuclei segmented and labeled
    
    """


    def smart_dilate(stack, labelmask, sensitivity, dilation_length):
        """
        Dilate nuclei, then apply a threshold to the newly-added pixels and
        only retains pixels that cross it. Change mask in place.
        """
        # Get mean pixel values of foreground and background and define threshold.
        bg_mean = np.mean(stack[labelmask == 0])
        fg_mean = np.mean(stack[labelmask > 0])
        t = bg_mean + ((fg_mean - bg_mean) * sensitivity)
        # Dilate labelmask, return as new mask.
        labelmask_dilated = labelmask_apply_morphology(labelmask, 
                mfunc=ndi.morphology.binary_dilation, 
                struct=np.ones((1, dilation_length, dilation_length)), 
                expand_size=(1, dilation_length + 1, dilation_length + 1))
        # Remove any pixels from dilated mask that are below threshhold.
        labelmask_dilated[stack < t] = 0
        # Add pixels matching nuc in dilated mask to old mask, pixels in old mask that are n
        # and 0 in dilated mask are kept at n. So dilation doesn't remove any nuclear pixels.
        for n in np.unique(labelmask)[1:]:
            if (n != 0):
                labelmask[labelmask_dilated == n] = n

    # Normalize each Z-slice to mean intensity to account for uneven illumination.
    stack = zstack_normalize_mean(instack)
    # Apply gaussian filter.
    stack_smooth = ndi.filters.gaussian_filter(stack, sigma=sigma1)
    # Threshold, make binary mask, fill.
    t = threshold_otsu(stack_smooth)
    mask = np.where(stack_smooth >= t, 1, 0)
    mask = imfill(mask, find_background_point(mask))
    # Use morphological erosion to remove spurious connections between objects.
    mask = ndi.morphology.binary_erosion(mask, structure=np.ones((1, erosion_length, erosion_length)))
    # Perform distance transform of mask.
    dist = ndi.distance_transform_edt(mask)
    # Find local maxima for watershed seeds.
    seeds, _ = peak_local_max_nD(dist, size=seed_window)
    # Add a background seed.
    seeds[find_background_point(mask)] = seeds.max() + 1
    # Re-smooth, do gradient transform to get substrate for watershedding.
    dog = dog_filter(stack, sigma_dog_small, sigma_dog_big)
    grad = gradient_nD(dog)
    # Remove nan from grad, replace with non-nan max values.
    grad[np.isnan(grad)] = grad[~np.isnan(grad)].max()
    # Segment by watershed algorithm.
    ws = watershed(grad, seeds.astype(int))
    # Filter nuclei for size and circularity.
    labelmask = labelmask_filter_objsize(ws, size_min, size_max)
    labelmask = filter_labelmask(labelmask, object_circularity, circularity_min, 1000)
    # Dilate labeled structures.
    smart_dilate(stack_smooth, labelmask, sensitivity, dilation_length)

    if (display):
        middle_slice = int(stack.shape[0] / 2)
        fig, ax = plt.subplots(3,2, figsize=(10,10))
        # Display mask.
        ax[0][0].imshow(mask.max(axis=0))
        ax[0][0].set_title('Initial Mask')
        # Display watershed seeds.
        seeds_vis = ndi.morphology.binary_dilation(seeds, structure=np.ones((1,8,8)))
        ax[0][1].imshow(stack_smooth.max(axis=0), alpha=0.5)
        ax[0][1].imshow(seeds_vis.max(axis=0), alpha=0.5)
        ax[0][1].set_title('Watershed seeds')
        # Display gradient.
        ax[1][0].imshow(grad[middle_slice])
        ax[1][0].set_title('Gradient')
        # Display watershed output.
        ax[1][1].imshow(ws.max(axis=0))
        ax[1][1].set_title('Watershed')
        # Display final mask.
        ax[2][0].imshow(labelmask.max(axis=0))
        ax[2][0].set_title('Final Segmentation')
        
    return labelmask

############################################################################
def segment_nuclei3D_monolayer(stack, sigma1=3, sigma_dog_big=15, 
        sigma_dog_small=5, seed_window=(30,30), min_seed_dist=25, 
        dilation_length=5, size_min=0, size_max=np.inf):
    """Segment nuclei from confocal nuclear monolayers
    
    Segment nuclei from nuclear monolayers, such as standard MS2 confocal
    stacks. Monolayers don't generally require 3D segmentation, so this
    function uses the max projection in Z to define the domain of each 
    nucleus in XY. 
    
    Args:
        stack: ndarray
            3D image stack of dimensions [z, x, y].
        sigma1: int
            Sigma for Gaussian smoothing used to make gradient input to watershed
        sigma_dog_small: int
            Smaller sigma for DoG filter used to create initial mask
        sigma_dog_big: int
            Larger sigma for DoG filter used to create initial mask
        seed_window: tuple of three ints
            Size in [z, x, y] for window for determining local maxes in distance
            transform. Generally want size to be ~ size of nuclei.
        min_seed_dist: numeric
            The minimum euclidean distance (in pixels) allowed between watershed
            seeds. Typically set as ~the diameter of the nuclei.
        size_min: int
            Minimum size, in pixels, of objects to retain
        size_max: int
            Maximum size, in pixels, of objects to retain
        dilation_length: int
            Size in x and y of structuring element for dilating objects after
            final segmentation.
        
    Returns:
        labelmask: ndarray
            2D labelmask of nuclei.
    """
    # Make max projection on Z.
    maxp = stack.max(axis=0)
    # Filter with DoG to make nuclei into blobs.
    dog = imp.dog_filter(maxp, sigma_dog_small, sigma_dog_big)
    # Get threshold, use thresh to make initial mask and fill holes.
    t = threshold_otsu(dog)
    mask = np.where(dog > t, 1, 0)
    mask = imfill(mask, find_background_point(mask))
    # Perform distance transform, find local maxima for watershed seeds.
    dist = ndi.distance_transform_edt(mask)
    seeds, _ = peak_local_max_nD(dist, size=seed_window, min_dist=min_seed_dist)
    # Smooth image and take gradient, use as input for watershed.
    im_smooth = ndi.filters.gaussian_filter(maxp, sigma=sigma1)
    grad = gradient_nD(im_smooth)
    ws = watershed(grad, seeds.astype(int))
    # Filter object size, relabel to set background to 0.
    labelmask = labelmask_filter_objsize(ws, size_min, size_max)
    labelmask = relabel_labelmask(labelmask)
    # Dilate segmented nuclei.
    labelmask = labelmask_apply_morphology(labelmask, 
                    mfunc=ndi.morphology.binary_dilation, 
                    struct=np.ones((dilation_length, dilation_length)), 
                    expand_size=(dilation_length + 1, dilation_length + 1))
    
    return labelmask

############################################################################
def update_labels(mask1, mask2):
    """Match labels of segmented structures to those of a previous frame.
    
    Uses a simple principle of reciprocal best hits: for each labeled object
    in mask 2, find the object in mask1 with the most overlapping pixels. 
    Then do the reverse: find the maximallly overlapping object in mask 1 for
    the objects in mask 2. For objects that are each other's best hit (most
    overlapping pixels), the labels in mask2 are replaced with those of mask1.
    Labels that do not have reciprocal best hits are dropped from the mask.
    
    Args:
        mask1: ndarray
            Labelmask in order [z, x, y]. Labels from this mask will be 
            propagated to mask2.
        mask2: ndarray
            Labelmask of same shape as mask1. Labels in this mask will be
            replaced by corresponding labels from mask1.
        
    Returns:
        updated_mask: ndarray
            Labelmask of identical shape to mask1 and mask2, updated to
            propagate labels from mask1 to mask2.
    
    Raises:
        ValueError:
            If the shapes of the two masks are not the same.
    """
    # Find the object in mask2 that has maximum overlap with an object in max1,
    # (as a fraction of the objects pixels in mask1)
    def get_max_overlap(mask1, mask2, label1):
        # Count overlapping pixels.
        labels, counts = np.unique(mask2[mask1 == label1], return_counts=True)
        # Sort labels by counts (ascending).
        labels_sorted = labels[np.argsort(counts)]
        counts_sorted = counts[np.argsort(counts)]
        # Select new label with maximum overlap.
        max_overlap = labels_sorted[-1]
        return max_overlap
    
    def main(mask1, mask2):
        if not (mask1.shape == mask2.shape):
            raise ValueError("Masks do not have the same shape.")
        # Initialize blank mask.
        updated_mask = np.zeros(mask2.shape)
        for label1 in np.unique(mask1)[1:]:
            # Find label in mask2 with maximum overlap with nuc from mask1.
            label2 = get_max_overlap(mask1, mask2, label1)
            # Check that labels are "reciprocal best hits" by determining the 
            # label in mask1 with maximum overlap with label in mask2 found above.
            label2_besthit = get_max_overlap(mask2, mask1, label2)
            if ((label2_besthit == label1) and (label1 != 0)):
                updated_mask[mask2 == label2] = label1
        return updated_mask

    return main(mask1, mask2)

############################################################################
def segment_nuclei4D(stack, seg_func, update_func, **kwargs):
    """Segment nuclei in a 4D image stack (expect lattice data).
    
    A wrapper for two supplied functions: one function that performs
    segmentation of a 3D image stack and a second function that connects
    segmentation outputs for consecutive frames by identifying shared objects
    and harmonizing their labels. Iteratively calls these functions on all
    3D stacks and returns a 4D labelmask of segmented objects contiguous in 
    time.
    
    Args:
        stack: ndarray
            4D image stack of dimensions [t, z, x, y].
        seg_func: function
            Function that performs segmentation on 3D image stacks. Must take 
            as arguments a 3D image stack and optional keyword arguments.
        update_func: function
            Function that compares two 3D labelmasks, assigns object IDs from 
            mask1 to mask2, and updates labels in mask2 to match mask1.
        **kwargs: optional key-word arguments
            Keyword arguments to supply to segmentation function.
    
    Returns:
        labelmask: ndarray
            4D labelmask of dimensions [t, z, x, y] with segmented objects.
    
    Example usage:
        labelmask = segment_nuclei4D(im_stack, segment_nuclei3D, update_labels,
            sigma=5, percentile=90)
    """
    # Create partial form of segmentation function with supplied kwargs.
    seg_func_p = partial(seg_func, **kwargs)
    # Segment first frame, add 4th axis in 0 position.
    labelmask = seg_func_p(stack[0])
    labelmask = np.expand_dims(labelmask, axis=0) 
    
    # Segment subsequent frames, update labels, build 4D labelmask.
    for t in range(1, stack.shape[0]):
        print(t)
        mask = seg_func_p(stack[t])
        mask_updated = update_func(labelmask[t-1], mask)
        mask_updated = np.expand_dims(mask_updated, axis=0)
        labelmask = np.concatenate((labelmask, mask_updated), axis=0)
    
    return labelmask

############################################################################
def lattice_segment_nuclei_5(stack, channel=1, **kwargs):
    """Wrapper for nuclear segmentation routine for lattice data.

    Uses 3D stack segmentation function segment_nuclei3D_4 and label propagator
    update_labels.

    Suggested workflow:

    1. Background subtract stack: 
        bgsub = stack_bgsub(stack)
    2. Test parameters to get good segmentation of first Z-stack:
        test = segment_nuclei3D_5(bgsub[1,0], seed_window=(70,50,50), display=True)
        viewer(test, 'zxy')
    3. Call this function on bgsub with optimized parameters.
        labelmask = lattice_segment_nuclei_4(bgsub, channel=1, seed_window=(70,40,40))
    
    Args:
        stack: ndarray
            5D image stack of dimensions [c, t, z, x, y].
        channel: int
            Channel (0th dimension) to use for segmentation.
        kwargs: key-word arguments (optional)
            Arguments for segment_nuclei3D_4
        
    Returns:
        labelmask: ndarray
            4D labelmask of dimensions [t, z, x, y]
    """

    return segment_nuclei4D(stack[channel], segment_nuclei3D_5, update_labels, **kwargs)

############################################################################
def segMS2_3dstack(stack, peak_window_size=(70,50,50), sigma_small=0.5, 
                   sigma_big=4, bg_radius=4, fitwindow_rad_xy=5, 
                   fitwindow_rad_z=9, h_stringency=1, 
                   xy_max_width=15):  
    """Segment MS2 spots from a 3D stack, fit them with 3D gaussian
    
    Alrigthm: bandbass filter -> background subtraction -> find local maxima
    -> fit gaussian to windows around maxima -> filter based on fit parameters
    -> label and return.
    
    Args:
        stack: ndarray
            3D image stack containing MS2 spots
        peak_window_size: tuple of three ints
            Size in [z,x,y] of window used to find local maxima. Typically
            set to the approximage dimensions of nuclei.
        sigma_small: numeric
            Lower sigma for difference-of-gaussians bandpass filter
        sigma_small: numeric
            Upper sigma for difference-of-gaussians bandpass filter
        bg_radius: int
            Radius for minimum filter used for background subtraction
        fitwindow_rad_xy: int
            Radius in pixels in the xy-dimension of the window around local
            maxima peaks within which to do gaussian fitting.
        fitwindow_rad_z: int
            Radius in pixels in the z-dimension of the window around local
            maxima peaks within which to do gaussian fitting.
        h_stringency: float
            Sets the filter for the minimum peak height of the gaussian fit
            for a spot to be retained, expressed as standard deviations
            above the mean pixel value for the entire 3D stack.
        xy_max_width: int
            Maximum width in xy-dimension used for filtering gaussian fits.
    
    Returns:
        spot_data: dict of ndarrays
            Data for detected spots. Dict keys are unique spot IDs (integers),
            array entries are 0: z-coordinate, 1: x-coordinate, 2: y-coordinate, 
            3: gaussian fit height, 4: gaussian fit z-width, 5: gaussian fit 
            x-width, 6: gaussian fit y-width.
    """
    def get_fitwindow(data, peak, xy_rad=5, z_rad=9):
        """Retrieve section of image stack corresponding to given
        window around a point"""
        zmin = max(0,peak[0] - z_rad)
        zmax = min(data.shape[0] - 1, peak[0] + z_rad)
        xmin = max(0,peak[1] - xy_rad)
        xmax = min(data.shape[1] - 1, peak[1] + xy_rad)
        ymin = max(0,peak[2] - xy_rad)
        ymax = min(data.shape[2] - 1, peak[2] + xy_rad)
        # Get adjustments in each direction — value to subtract from relative
        # coordinates to center them at 0,0,0 in the window center.
        z_adj, x_adj, y_adj = int((zmax-zmin)/2), int((xmax-xmin)/2), int((ymax-ymin)/2)
        return data[zmin:(zmax+1), xmin:(xmax+1), ymin:(ymax+1)], z_adj, x_adj, y_adj
    
    def relabel(peak_ids, oldparams, mask):
        """Renumber labelmask and corresponding fit parameters
        Set background as 0, objects in order 1...end.
        """
        spot_data = {}
        peak_num = 1
        for peak in peak_ids:
            #coords = np.where(mask == peak)
            paramsnew = oldparams[peak-1,:] # object 1 will be fitparams row 0
            # Rearrange params from fit function so coordinates lead.
            spot_data[peak_num] = paramsnew[[1,2,3,0,4,5,6]]
            peak_num = peak_num + 1
        return spot_data

    def clamp(n, minn, maxn):
        """Bound a number between two constants"""
        return max(min(maxn, n), minn)
    
    # Filter and background subtract image.
    dog = dog_filter(stack, sigma_small, sigma_big)
    bg = ndi.filters.minimum_filter(dog, bg_radius)
    dog_bs = dog - bg
    # Make a labelmask corresponding to local maxima peaks.
    mask, peaks = peak_local_max_nD(dog_bs, peak_window_size)
    
    # Fit 3D gaussian in window surrounding each local maximum.
    fitparams = np.ndarray((0,7))
    for peak in peaks:
        fitwindow, z_adj, x_adj, y_adj = get_fitwindow(stack, peak, fitwindow_rad_xy, 
                                            fitwindow_rad_z)
        opt = fitgaussian3d(fitwindow)
        if opt.success:
            peak_fitparams = opt.x
            # Move center coordinates to match center of gaussian fit, ensure they're within image.
            peak_fitparams[1] = clamp(int(peak[0] + peak_fitparams[1] - z_adj), 0, stack.shape[-3]-1)
            peak_fitparams[2] = clamp(int(peak[1] + peak_fitparams[2] - x_adj), 0, stack.shape[-2]-1)
            peak_fitparams[3] = clamp(int(peak[2] + peak_fitparams[3] - y_adj), 0, stack.shape[-1]-1)
            fitparams = np.vstack((fitparams, opt.x))
        # If fit fails, add dummy entry for spot.
        else:
            fitparams = np.vstack((fitparams, np.array([0,0,0,0,1e6,1e6,1e6])))
    
    # Find threshold for gaussian height (intensity for 3D).
    mean_ = np.mean(stack)
    std = np.std(stack)
    t = mean_ + (std * h_stringency)
    # Filter peaks based on guassian fit parameters.
    peak_ids = np.unique(mask)[1:]
    # fitparams columns: 0: height, 5: x_width, 6: y_width
    trupeaks = peak_ids[(fitparams[:,0] > t) 
                        & (np.mean(fitparams[:,5:6], axis=1) < xy_max_width)]
    spot_data = relabel(trupeaks, fitparams, mask)
    return spot_data

############################################################################
def add_ms2_frame(spot_data, newframe_spotdata, nucmask, t, 
                  max_frame_gap=1, max_jump=10, scale_xy=1, scale_z=1):
    """Add spot detections for new frame to detection data for previous frames.
    
    Spots detected in new frame are connected to spots in previous frames
    if they are within specified distance (max_jump). Spots can "disappear" 
    for a number of frames defined by max_frame_gap. Spots that cannot be 
    connected to spots from prior frames are initialized as new spots.
    
    Args:
        spot_data: dict of ndarrays
            Data containing tracking of spots detected in previous frames.
            Dict entries are unique spot IDs (numeric 1...), rows of ndarray
            are detections of the spot in a single frame. Column order is
            0: frame no. (time), 1: nucleus ID, 2: z-coordinate, 3: x-
            coordinate, 4: y-coordinate, 5: gaussian fit height, 6: gaussian
            fit z-width, 7: gaussian fit x-width, 8: gaussian fit y-width.
        newframe_spotdata: dict of ndarrays
            Data for detected spots in frame to be added, returned from MS2
            dot segmentation function. Dict keys are unique spot IDs (integers),
            array entries are 0: z-coordinate, 1: x-coordinate, 2: y-coordinate, 
            3: gaussian fit height, 4: gaussian fit z-width, 5: gaussian fit 
            x-width, 6: gaussian fit y-width.
        nucmask: ndarray
            4D labelmask of dimensions [t,z,x,y] of segmented nuclei. 0 is 
            background (not a nucleus) and nuclei have integer labels.
        t: int
            Number of frame to be added.
        max_frame_gap: int
            Maximum number of frames from which spot can be absent and still
            connected across the gap. Example: for a value of 1, a spot
            detected in frame 6 and absent from frame 7 can be connected to
            a spot in frame 8, but a spot in frame 5 cannot be connected to
            frame 8 if it is absent in frames 6 and 7.
        max_jump: numeric
            Maximum 3D displacement between frames for two spots to be connected
        scale_xy: numeric
            Distance scale for xy direction (typically: nm per pixel)
        scale_z: numeric
            Distance scale for z direction (typically: nm per pixel)
        
    Returns:
        spot_data: dict of ndarrays
            Same structure as input spot_data with data from new frame added.
    """
    def initialize_new_spot(new_spot_data, spot_data):
        """Initialize new spot with next numeric ID and entry in spot_data."""
        if (spot_data.keys()):
            new_id = max(spot_data.keys()) + 1
        else:
            new_id = 1
        spot_data[new_id] = np.expand_dims(new_spot_data, 0)

    def sq_euc_distance(coords1, coords2, scale_z=1, scale_xy=1):
        """Find the squared euclidean distance between two points."""
        z2 = ((coords2[0] - coords1[0]) * scale_z) ** 2
        x2 = ((coords2[1] - coords1[1]) * scale_xy) ** 2
        y2 = ((coords2[2] - coords1[2]) * scale_xy) ** 2
        sed = z2 + x2 + y2
        return sed
    
    # Make a list of coordinates for all spots in a frame
    def coord_list_t(spot_data, t):
        """Make a list of [z,x,y] coordinate tuples for all spots in a given
        frame"""
        coord_list = []
        for spot_id in spot_data:
            this_spot_data = spot_data[spot_id]
            row = this_spot_data[this_spot_data[:,0] == t]
            if (len(row) > 0):
                row = list(row[0])
                spot_coords = [spot_id] + row[2:5]
                coord_list.append(spot_coords)
        return coord_list
            
    
    def find_nearest_spot(this_coord, coord_list, scale_z, scale_xy):
        """For a given point, find the closest spot in a coordinate list
        and the distance between the points."""
        closest_sed = np.inf
        closest_spot = 0
        for test_data in coord_list:
            test_spot_id = test_data[0]
            test_coords = (test_data[1:4])
            sed = sq_euc_distance(test_coords, this_coord, scale_z, scale_xy)
            if (sed < closest_sed):
                closest_sed = sed
                closest_spot = test_spot_id
                closest_spot_coords = test_coords
        return closest_spot, np.sqrt(closest_sed), closest_spot_coords

    def update_spot(this_spot_data, spot_data, scale_z, scale_xy, max_frame_gap, 
                    t):
        """Walk back one frame at a time within limit set by maximum gap, search 
        for a nearest spot that is within the maximum allowable jump, handle 
        duplicates, add connected points to spot_data."""
        this_spot_coords = (this_spot_data[2:5])
        # Walk back one frame at a time.
        for t_lag in range(1, max_frame_gap + 2):
            if ((t - t_lag) >= 0):
                # Get nearest spot in the current frame.
                spot_coords_tlag = coord_list_t(spot_data, t - t_lag)
                # If there are no previously detected spots, break from for loop and initialize new spot entry.
                if (len(spot_coords_tlag) == 0):
                    break
                nearest_spot_id, dist, nearest_spot_coords = find_nearest_spot(this_spot_coords, spot_coords_tlag, scale_z, scale_xy)
                # Check is spot is within max distance.
                if (dist <= max_jump):
                    this_spot_nucID = this_spot_data[1]
                    nearest_spot_nucID = spot_data[nearest_spot_id][-1,1]
                    # Check if there's already a spot added for this time.
                    existing = spot_data[nearest_spot_id][spot_data[nearest_spot_id][:,0] == t]
                    # If there's no existing spot, add this spot to the end of the data for connected spot.
                    if (len(existing) == 0):
                        spot_data[nearest_spot_id] = np.append(spot_data[nearest_spot_id], [this_spot_data], axis=0)
                        return
                    # If there is an existing spot, if the current spot is closer to the previous-frame spot
                    # than the existing entry, replace it. Otherwise, continue looking in previous frames (if
                    # applicable) and eventually create new spot after for loop. I'm not sure this is the best
                    # behavior--may consider dumping out of for loop and creating new spot rather than looking
                    # to previous frames in this situation.
                    else:
                        existing_dist = np.sqrt(sq_euc_distance(nearest_spot_coords, existing[0,2:5], scale_z, scale_xy))
                        # If the the current spot is closer than the existing spot, replace 
                        # existing and initialize it as a new spot.
                        if (dist < existing_dist):
                            row_index = np.where(spot_data[nearest_spot_id][:,0] == t)[0][0]
                            superseded_spot_data = spot_data[nearest_spot_id][row_index]
                            # Superseded spot from this frame gets bumped to be a new spot.
                            initialize_new_spot(superseded_spot_data, spot_data)
                            # Replace data for superseded spot with this spot's data.
                            spot_data[nearest_spot_id][row_index] = this_spot_data
                            return

        # If no suitable spot was found in previous frames, make a new spot.
        initialize_new_spot(this_spot_data, spot_data)
        
    
    # Main
    spot_data = spot_data.copy()
    # Go through each spot in the new mask
    for this_spot_id in newframe_spotdata:
        spot_coords = tuple(np.append([t], newframe_spotdata[this_spot_id][0:3]).astype(int))
        nuc_id = nucmask[spot_coords]
        # Add time and nuclear ID columns to spot data and call update to search 
        # for connected spots in previous frames.
        this_spot_data = np.append([t, nuc_id], newframe_spotdata[this_spot_id])
        update_spot(this_spot_data, spot_data, scale_z, scale_xy, max_frame_gap, t)
    return spot_data   

############################################################################
def ms2_segment_stack(stack, nucmask, channel=0, seg_func=segMS2_3dstack, 
    max_frame_gap=1, max_jump=10, scale_xy=1, scale_z=1, **kwargs):
    """Detect and segment MS2 spots from a 5D image stack.
    
    Mostly a wrapper for MS2 detection function and spot connector function
    add_ms2_frame. Initializes spot_data structure using segmentation of 
    frame 0, then calls detector function and connector on each subsequent
    frame. Is modular with respect to segmentation function: a new function
    receives args from *kwargs. Connector is hard-coded as add_ms2_frame.
    
    Args:
        stack: ndarray
            5D image stack of dimensions [c,t,z,x,y] containing MS2 spots
        channel: int
            Channel containing MS2 spots
        nucmask: ndarray
            4D labelmask of dimensions [t,z,x,y] of segmented nuclei. 0 is 
            background (not a nucleus) and nuclei have integer labels.
        seg_func: function
            Function that performs segmentation of MS2 dots in a 3D stack
        max_frame_gap: int
            Maximum number of frames from which spot can be absent and still
            connected across the gap. Example: for a value of 1, a spot
            detected in frame 6 and absent from frame 7 can be connected to
            a spot in frame 8, but a spot in frame 5 cannot be connected to
            frame 8 if it is absent in frames 6 and 7.
        max_jump: numeric
            Maximum 3D displacement between frames for two spots to be connected
        scale_xy: numeric
            Distance scale for xy direction (typically: nm per pixel)
        scale_z: numeric
            Distance scale for z direction (typically: nm per pixel)
        *kwargs: key-word arguments
            Args supplied to segmentation function
        
    Returns:
        spot_data: dict of ndarrays
            Data containing tracking of spots detected. Dict entries are unique 
            spot IDs (numeric 1...), rows of ndarray are detections of the spot 
            in a single frame. Column order is 0: frame no. (time), 1: nucleus 
            ID, 2: z-coordinate, 3: x-coordinate, 4: y-coordinate, 5: gaussian 
            fit height, 6: gaussian fit z-width, 7: gaussian fit x-width, 
            8: gaussian fit y-width.   
    """
    
    def init_spot_data(data_f0, nucmask):
        """Initialize spot_data dict from data for first frame. Filters out spots 
        that are not in nuclei, relabels remaining spots 1...end, adds time 0 and
        nucleus id to each data entry."""
        spot_id = 1
        spot_data = {}
        for n in data_f0:
            spot_coords = tuple(np.append([0], data_f0[n][0:3]).astype(int))
            nuc_id = nucmask[spot_coords]
            spot_data[spot_id] = np.expand_dims(np.append([0, nuc_id], data_f0[n]), 0)
            spot_id = spot_id + 1
        return spot_data
        
    # Segment first frame and initialize spot data
    nframes = stack[channel].shape[0]
    spot_data_f0 = seg_func(stack[channel, 0], **kwargs)
    spot_data = init_spot_data(spot_data_f0, nucmask)

    # Segment and connect subsequent frames.
    for t in range(1, nframes):
        substack = stack[channel, t]
        print(t)
        frame_data = seg_func(substack, **kwargs)
        spot_data = add_ms2_frame(spot_data, frame_data, nucmask, t, max_frame_gap=max_frame_gap,
            max_jump=max_jump, scale_xy=scale_xy, scale_z=scale_z)
    return spot_data

############################################################################
# Functions for analyzing segmented images
############################################################################

def add_volume_mean(spot_data, stack, channel, ij_rad, z_rad, ij_scale=1, z_scale=1):
    """Find mean volume within ellipsoid centered on spots, add to spot_info

    Args:
        spot_data: dict of ndarrays
            Data containing tracking of spots detected in previous frames.
            Dict entries are unique spot IDs (numeric 1...), rows of ndarray
            are detections of the spot in a single frame. Column order is
            0: frame no. (time), 1: nucleus ID, 2: z-coordinate, 3: x-
            coordinate, 4: y-coordinate, 5: gaussian fit height, 6: gaussian
            fit z-width, 7: gaussian fit x-width, 8: gaussian fit y-width.
        stack: ndarray
            Image stack of dimensions [c,t,z,x,y]
        channel: int
            Channel containing MS2 spots
        ij_rad: numeric
            Radius in real units of ellipsoid in the ij (xy) dimension.
        z_rad: numeric
            Radius in real units of ellipsoid in the z dimension.
        ij_scale: numeric
            Scale factor for ij_rad (typically nm/pixel)
        z_scale: numeric
            Scale factor for z_rad (typically nm/pixel)
    
    Returns:
        spot_data: dict of ndarrays
            Input dictionary with mean ellipsoid pixel values appended as an 
            additional column (9) to all entries.
    """
    def ellipsoid_mean(coords, stack, meshgrid, ij_rad, z_rad):
        """Define ellipsoid around point, return mean of pixel values in ellipsoid."""
        # Equation: (x-x0)^2 + (y-y0)^2 + a(z-z0)^2 = r^2
        r = ij_rad # r is just more intuitive for me to think about...
        a = (r ** 2) / (z_rad ** 2)
        z0, i0, j0 = coords
        valsgrid = np.sqrt((a * ((meshgrid[0] - z0) ** 2)) + ((meshgrid[1] - i0) ** 2) + ((meshgrid[2] - j0) ** 2))
        pixels = stack[valsgrid <= r]
        return pixels.mean()
    
    spot_data = spot_data.copy()
    # Make meshgrid for stack.
    meshgrid = mesh_like(stack, 3)
    # Scale radii to pixels.
    ij_rad_pix = ij_rad / ij_scale
    z_rad_pix = z_rad / z_scale
    # Update data for each spot at each time point combination by adding column
    # with the sum of the pixel values within defined ellipses.
    for spot_id in spot_data:
        spot_array = spot_data[spot_id]
        # Initialize new array with extra column.
        new_array = np.ndarray((spot_array.shape[0], spot_array.shape[1] + 1))
        for rownum in range(0, spot_array.shape[0]):
            row = spot_array[rownum]
            t = int(row[0])
            coords = tuple(row[2:5].astype(int))
            substack = stack[channel, t]
            pix_mean = ellipsoid_mean(coords, substack, meshgrid, ij_rad_pix, z_rad_pix)
            new_array[rownum] = np.append(row, [pix_mean])
        spot_data[spot_id] = new_array
    return spot_data

############################################################################
def add_gaussian_integration(spot_data, wlength_xy, wlength_z):
    """Add a column to spot_data that integrates intensity from gaussian fit
    
    For each spot in spot_data, uses gaussian fit parameters (height and 
    widths in z,y,x) to integrate the gaussian function within a window of 
    supplied dimensions ([z,x,y] = [wlength_z, wlength_xy, wlength_xy]). 
    "Integration" is discrete — gaussian function is converted to pixel values, 
    and the mean pixel intensity is then added as an additional column to each 
    entry in spot_data. Mean is used over sum simply to keep numbers low and 
    aid interpretability.
    
    Args:
        spot_data: dict of ndarrays
            Data containing tracking of spots detected. Dict entries are unique 
            spot IDs (numeric 1...), rows of ndarray are detections of the spot 
            in a single frame. Required columns: 5: gaussian fit height, 6: 
            gaussian fit z-width, 7: gaussian fit x-width, 8: gaussian fit 
            y-width.
        wlength_xy: int
            Length of the sides of the window used for integration in the
            lateral dimension. Must be an odd number.
        wlength_z: int
            Length of the sides of the window used for integration in the
            axial dimension. Must be an odd number.
            
    Returns:
        spot_data: dict of ndarrays
            Structure identical to input with an additional column appended to
            all entries containing result of integration.
    """
    def integrate_gaussian(p, wlength_xy, wlength_z):
        """Determine mean pixel intensity within a window given parameters
        of a 3D gaussian function."""
        if ((wlength_xy % 2 == 0) or (wlength_z % 2 == 0)):
            raise ValueError('wlength_xy and wlength_z must be odd.')
        # Get fit parameters, find coords for center pixel within window.    
        h, width_z, width_x, width_y = p[5:9]
        center_xy = int(wlength_xy / 2)
        center_z = int(wlength_z / 2)
        # Get indices for the window.
        z,x,y = np.indices((wlength_z, wlength_xy, wlength_xy))
        # Generate function to receive indexes and return values of gaussian 
        # function with given parameters
        f = gaussian3d(h, center_z, center_xy, center_xy, width_z, width_x, width_y)
        # Generate window with intensity values from 3d gaussian function.
        vals = f(z,x,y)
        # Return mean pixel intensity of window.
        return vals.mean()
    
    # Work on a copy of input data.
    spot_data = spot_data.copy()
    for spot_id in spot_data:
        spot_array = spot_data[spot_id]
        # Initialize new array with extra column.
        new_array = np.ndarray((spot_array.shape[0], spot_array.shape[1] + 1))
        for rownum in range(0, spot_array.shape[0]):
            row = spot_array[rownum]
            pix_mean = integrate_gaussian(row, wlength_xy, wlength_z)
            new_array[rownum] = np.append(row, [pix_mean])
        spot_data[spot_id] = new_array
    return spot_data