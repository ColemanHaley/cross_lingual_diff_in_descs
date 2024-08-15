import streamlit as st
from st_clickable_images import clickable_images
import base64

download_images_button_ind = 0

def plot_clickable_images(iids):
    global download_images_button_ind

    image_names = [f'{hex(iid)[2:].zfill(16)}.jpg' for iid in iids]
    st.download_button('List of names of the images', '\n'.join(image_names), key=f'download_images{download_images_button_ind}')
    download_images_button_ind += 1
    image_file_paths = [f'/mnt/c/Users/uribe/PycharmProjects/datasets/crossmodal3600/images/{hex(iid)[2:].zfill(16)}.jpg' for iid in iids]
    images = []
    for file in image_file_paths:
        with open(file, "rb") as image:
            encoded = base64.b64encode(image.read()).decode()
            images.append(f"data:image/jpeg;base64,{encoded}")

    clicked = clickable_images(
        images,
        titles=[f"Image #{str(i)}" for i in range(len(image_file_paths))],
        div_style={"display": "flex", "justify-content": "center", "flex-wrap": "wrap"},
        img_style={"margin": "5px", "height": "200px"}
    )

    return clicked
