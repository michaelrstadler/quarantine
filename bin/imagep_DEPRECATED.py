"""
Functions that once belonged to imagep.py. I move things here that I doubt
I will ever want to use again, but that may contain useful code or ideas
that I could mine.
"""

############################################################################
def segment_nuclei3D_1(stack, sigma=4, percentile=95, size_max=2e5, 
                     size_min=5000, erode_by=5):
    """Segment nuclei from a single 3D lattice stack.
    
    Details: Segments nuclei in lattice light sheet image substack. Uses
    gaussian smoothing and thresholding with a simple percentile to
    generate initial nuclear mask, then erodes this mask slightly, con-
    nects components, filters resulting objects for size, and returns
    a 3D labelmask of filtered structures.
    
    Optional: Input can be pre-segmented from background by segment_embryo
    function. This can help to standardize use of percentile-based 
    thresholding.
    
    Args:
        stack: 3D ndarray
            Image stack in order [z, x, y]. This is a representative 
            substack (single channel and timepoint) of the full stack
            on which to perform segmentation.
        sigma: int
            Sigma value to use for gaussian smoothing
        percentile: int
            Percentile value to use for thresholding. Only non-zero pixels
            are used in calculating percentiles.
        size_max: int
            Upper size cutoff for connected structures (nuclei)
        size_min: int
            Lower size cutoff for connected structures
        erode_by: int
            Size of the structuring element (in x-y only) used to erode
            preliminary thresholded mask.
            
    Returns:
        labelmask: ndarray
            Same shape as input stack, filtered segmented structures are 
            masked by unique integer labels.
    """
    # Smooth input image.
    stack_smooth = ndi.filters.gaussian_filter(stack, sigma=sigma)
    # Assign threshold value based on percentile of non-zero pixels, mask on threshold.
    t = np.percentile(stack_smooth[stack_smooth > 0], percentile);
    mask = np.where(stack_smooth > t, True, False)
    # Erode binary mask.
    mask = ndi.morphology.binary_erosion(mask, structure=np.ones((1, erode_by, erode_by)))
    # Label connected components to generate label mask.
    conn_comp, info = ndi.label(mask)
    # Filter labelmask based on maximum and minimum structure size.
    (labels, counts) = np.unique(conn_comp, return_counts=True)
    labels_selected = labels[(counts >= size_min) & (counts <= size_max)]
    labelmask = np.where(np.isin(conn_comp, labels_selected), conn_comp, 0)
    return labelmask

############################################################################
def segment_nuclei3D_2(stack, sigma_seeding=3,sigma_watershed=12, sigma_dist=2, window_size=(70, 100, 100),
                       closing_length=10, dilation_length = 7, size_max=2e5, size_min=5000, 
                       display=False):
    """Segment nuclei from a single 3D lattice stack.
    
    Details: Segments nuclei in lattice light sheet 3D image substack. Uses
    a gradient filter to find edges of nuclei, fills them, finds their 
    centers, and performs watershed segmentation. Resulting objects are 
    filtered for size and roundness and a 3D labelmask of nuclei is returned.
    
    Args:
        stack: 3D ndarray
            Image stack in order [z, x, y].
        sigma: int
            Sigma value to use for gaussian smoothing of original image.
        dist_sigma: int
            Sigma value to use for gaussian smoothing of distance transform
            of gradient.
        closing_length: int
            Side length of the structuring unit for morphological closing of 
            thresholded gradient mask.
        dilation_length: int
            Side length of the structuring unit for morphological dilation of 
            final mask.
        window_size: tuple of ints
            Size of the window for finding local maxima to seed watershed. 
            The sizes are the dimensions of the filter used to search for 
            maxima. So a size of (100, 100) will use a square with side lengths 
            of 100 pixels. Generally, you want the size dimensions to match 
            the dimensions of the objects you're searching for.
        size_max: int
            Maximum size in pixels allowed for segmented nuclei.
        size_min: int
            Minimum size in pixels allowed for segmented nuclei.
        display: bool
            If true, displays segmentation intermediates.
            
    Returns:
        labelmask: ndarray
            Same shape as input stack, filtered segmented structures are 
            masked by unique integer labels.
    """
    # Smooth stack.
    stack_smooth1 = ndi.filters.gaussian_filter(stack, sigma=sigma_seeding)

    # Apply 3D gradient filter.
    grad_seeding = gradient_nD(stack_smooth1)

    # Threshold.
    t = threshold_li(grad_seeding)
    mask = np.where(grad_seeding >= t, 1, 0)

    # Close with morphological filter.
    #mask = ndi.morphology.binary_closing(mask, structure=np.ones((1, closing_length, closing_length)))
    
    # Fill in holes.
    mask = imfill(mask)
    
    # Do a distance transform, smooth, find peaks.
    dist = ndi.distance_transform_edt(mask)
    dist = ndi.filters.gaussian_filter(dist, sigma_dist)
    seed_mask, seeds = peak_local_max_nD(dist, window_size)
    
    # DEPRECATED: Subtract mean background from gradient image.
    #grad_mean = grad.mean()
    #grad_bgsub = np.where(grad >= grad_mean, grad, 1)
    # Perform watershed segmentation on subtracted gradient with seeds.

    # Smooth stack.
    stack_smooth2 = ndi.filters.gaussian_filter(stack, sigma=sigma_watershed)

    # Apply 3D gradient filter.
    grad_watershed = gradient_nD(stack_smooth2)
    ws = ski.segmentation.watershed(grad_watershed, seed_mask.astype(int))
   
    # Filter segmented objects for maximum and minimum size.
    (labels, counts) = np.unique(ws, return_counts=True)
    labels_selected = labels[(counts >= size_min) & (counts <= size_max)]
    labelmask = np.where(np.isin(ws, labels_selected), ws, 0)
    
    # Close and slightly dilate resulting mask, re-label.
    labelmask = ndi.morphology.binary_closing(labelmask, structure=np.ones((1, closing_length, closing_length)))
    labelmask = ndi.morphology.binary_dilation(labelmask, structure=np.ones((1, dilation_length, dilation_length)))
    labelmask, info = ndi.label(labelmask)
    
    # Display segmentation intermediates if called.
    if (display):
        fig, ax = plt.subplots(3,3, figsize=(9,9))
        ax[0][0].set_title('Smoothed seeding input')
        ax[0][0].imshow(stack_smooth1.max(axis=0))
        ax[0][1].set_title('Seeding gradient')
        ax[0][1].imshow(grad_seeding.max(axis=0))
        ax[0][2].set_title('Mask')
        ax[0][2].imshow(mask.max(axis=0))
        ax[1][0].set_title('Dist. trnsfm')
        ax[1][0].imshow(dist.max(axis=0))
        ax[1][1].set_title('Watershed seeds')
        # Dilate seed mask to make single pixels visible.
        seed_mask_visible = ndi.morphology.binary_dilation(seed_mask, structure=np.ones((3,15,15)))
        ax[1][1].imshow(stack_smooth1.max(axis=0), alpha=0.5)
        ax[1][1].imshow(seed_mask_visible.max(axis=0), alpha=0.5)
        ax[1][2].set_title('Smoothed watershed input')
        ax[1][2].imshow(stack_smooth2.max(axis=0))
        ax[2][0].set_title('Watershed gradient')
        ax[2][0].imshow(grad_watershed.max(axis=0))
        ax[2][1].set_title('Seg final')
        ax[2][1].imshow(labelmask.max(axis=0), cmap="prism")
        plt.tight_layout()
        
    return labelmask


def lattice_segment_nuclei_1(stack, channel=1, **kwargs):
    """Wrapper for nuclear segmentation routine for lattice data.

    Uses 3D stack segmentation function segment_nuclei3D and label propagator
    update_labels
    
    Args:
        stack: ndarray
            5D image stack of dimensions [c, t, z, x, y].
        channel: int
            Channel (0th dimension) to use for segmentation.
        kwargs: key-word arguments (optional)
            Arguments for 3D segmentation function
        
    Returns:
        labelmask: ndarray
            4D labelmask of dimensions [t, z, x, y]
    
    
    """
    return segment_nuclei4D(stack[channel], segment_nuclei3D, update_labels, **kwargs)

def lattice_segment_nuclei_2(stack, channel=1, **kwargs):
    """Wrapper for nuclear segmentation routine for lattice data.

    Uses 3D stack segmentation function segment_nuclei3D_2 and label propagator
    update_labels
    
    Args:
        stack: ndarray
            5D image stack of dimensions [c, t, z, x, y].
        channel: int
            Channel (0th dimension) to use for segmentation.
        kwargs: key-word arguments (optional)
            Arguments for 3D segmentation function
        
    Returns:
        labelmask: ndarray
            4D labelmask of dimensions [t, z, x, y]
    
    
    """
    return segment_nuclei4D(stack[channel], segment_nuclei3D_2, update_labels, **kwargs)   


def mask_plane(stack, top_z0, bottom_z0, top_zmax, side='>', maskval=0):
    """Draw a plane through a 3D image and mask all positions on one side of 
       it.

    Args:
        stack: ndarray
            Image stack in order [..., x, y]
        
        ## Note: any three points will work as three points define a plane,
        ## the position descriptions here are just convenient suggestions.
        top_z0: tuple of three ints
            Point in the plane at the top of the image in slice z=0.
        bottom_z0: tuple of three ints
            Point in the plane at the bottom of the image in slice z=0.
        top_zmax: tuple of three ints
            Point in the plane at the top of the image in last z slice.
        
        side: string
            '>' or '<' determine side of plane on which to mask indices (for
            embryo border masking, > masks right, < masks left)
        maskval: int
            Value with which to replace masked values.
            
    Returns:
        stack_masked: ndarray
            Image stack with same dimensions as input stack, masked.
    
    Raises:
        ValueError: side isn't '<' or '>'.

    """
    # Recursive function that applies 3D mask to entire n-dimensional stack
    def _apply_mask(substack, mesh, d, side, maskval):
        # If 3-d stack, apply mask
        # Note: changes occur in place; don't have to return up the recursion chain
        if (len(substack.shape) == 3):            
            if side == '>':
                substack[mesh > d] = maskval
            elif side == '<':
                substack[mesh < d] = maskval
        # If not 3-d stack, call self on each substack of left-most dimension  
        else:
            for n in range(0, substack.shape[0]):
                _apply_mask(substack[n,...], mesh, d, side, maskval)
    
    if side not in {'<', '>'}:
        raise ValueError("side must be < or >") 
    
    # Using z, i, j notation throughout 
    max_i = stack.shape[-2] - 1
    max_z = stack.shape[-3] - 1
    
    ## Use vector solution to find equation of plane given three points.
    # Define 3 points in the plane.
    p1 = np.array(top_z0)
    p2 = np.array(bottom_z0)
    p3 = np.array(top_zmax)

    # Define two vectors that lie in the plane.
    v1 = p3 - p1
    v2 = p2 - p1

    # Take their cross product to produce a vector normal to the plane, this 
    # vector provides coefficients for equation in form ax + by + cz = d
    cp = np.cross(v1, v2)
    a, b, c = cp

    # To solve for d (az + bi + cj = d), take dot product of normal vector and 
    # any point in plane.
    d = np.dot(cp, p3)
    
    ## Make 3D mesh grid where value is the position in the given direction
    # Make linear vectors used to construct meshgrids.
    z = np.arange(0, stack.shape[-3])
    i = np.arange(0, stack.shape[-2])
    j = np.arange(0, stack.shape[-1])
    # Make meshgrids from vectors.
    zmesh, imesh, jmesh = np.meshgrid(z, i, j, sparse=False, indexing='ij')
    # Make mesh_sum array from line equation, value at each position is right 
    # side of line equation
    mesh_sum = a*zmesh + b*imesh + c*jmesh
    stack_masked = np.copy(stack)
    _apply_mask(stack_masked, mesh_sum, d, side, maskval)
    return(stack_masked)